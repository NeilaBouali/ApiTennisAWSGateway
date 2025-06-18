from fastapi import FastAPI, Request, HTTPException, Form, Query
from fastapi.responses import RedirectResponse
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional
from botocore.exceptions import ClientError
import io, csv, time, uuid, boto3
from fastapi.responses import JSONResponse
from fastapi import HTTPException
import json
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Le Tennis à PARIS 🇫🇷")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Feedback(BaseModel):
    accepted: bool 
    
# Monterr /static et /templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def get_glue_client():
    return boto3.client("glue", region_name="eu-west-3")

def get_athena_client():
    return boto3.client("athena", region_name="eu-west-3")

s3 = boto3.client("s3")
ATHENA_OUTPUT_PREVIEW = "s3://data-project-2025/Api/results/"
ATHENA_OUTPUT_SAVE = "s3://data-project-2025/Api/saved/"


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    glue = get_glue_client()
    # ici vous pouvez ou non réactiver votre code Glue/SQL, 
    # mais attention, un appel AWS lent va bloquer la page !
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/save", response_class=HTMLResponse)
async def save_to_s3(
    request: Request,
    database: str = Form(...),
    table:    str = Form(...),
    query:    str = Form(...)
):
    """
    Réécrit le résultat de la requête Athena dans S3 sous saved/ et renvoie un lien pré-signé.
    """
    athena = get_athena_client()
    sql = query.strip()
    prefix = ATHENA_OUTPUT_SAVE.rstrip("/") + f"/{uuid.uuid4()}/"
    # Lancer et attendre la requête
    exec_id = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        ResultConfiguration={"OutputLocation": prefix}
    )["QueryExecutionId"]
    while True:
        resp = athena.get_query_execution(QueryExecutionId=exec_id)
        st = resp["QueryExecution"]["Status"]["State"]
        if st in ("SUCCEEDED","FAILED","CANCELLED"):
            break
        time.sleep(0.5)
    if st != "SUCCEEDED":
        reason = resp["QueryExecution"]["Status"].get("StateChangeReason","")
        raise HTTPException(500, f"Athena query {st}" + (f": {reason}" if reason else ""))

    # Construire le chemin S3 du CSV (Athena écrit <exec_id>.csv)
    # e.g. s3://mon-bucket-athena/saved/{uuid}/{exec_id}.csv
    # On décompose :
    bucket = prefix.split("/")[2]
    key    = "/".join(prefix.split("/")[3:]) + f"{exec_id}.csv"

    # Générer URL pré-signée (valide 1h)
    presigned = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=3600
    )

    # Répondre avec un mini-template HTML
    return HTMLResponse(f"""
    <html>
    <head><title>Résultat sauvegardé</title>
      <link rel="stylesheet" href="/static/style.css">
    </head>
    <body>
      <header>
        <div class="stripe blue"></div><div class="stripe white"></div><div class="stripe red"></div>
        <h1>✅ Enregistré dans S3</h1>
      </header>
      <main style="padding:24px; text-align:center;">
        <p>Votre résultat a été écrit dans :</p>
        <code>{key}</code>
        <p><a class="btn" href="{presigned}" target="_blank">↗️ Télécharger depuis S3 (1 h)</a></p>
        <p><a class="btn-back" href="/">← Retour à l’interface</a></p>
      </main>
    </body>
    </html>
    """, status_code=200)



@app.get("/download", response_class=StreamingResponse)
async def download_csv(
    exec_id:  str   = Query(..., description="ID d'exécution Athena"),
    database: str   = Query(..., description="Nom de la base Glue"),
    table:    str   = Query(..., description="Nom de la table"),
):
    athena = get_athena_client()

    # 1) Récupérer tous les résultats (pagination avec NextToken)
    all_rows = []
    # Récupère les entêtes une première fois
    first_page = athena.get_query_results(QueryExecutionId=exec_id, MaxResults=1000)
    cols = [c["Label"] for c in first_page["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]]
    rows = [
        [d.get("VarCharValue", "") for d in row["Data"]]
        for row in first_page["ResultSet"]["Rows"][1:]
    ]
    all_rows.extend(rows)

    next_token = first_page.get("NextToken")
    # Boucle tant qu’il y a un NextToken
    while next_token:
        page = athena.get_query_results(
            QueryExecutionId=exec_id,
            MaxResults=1000,
            NextToken=next_token
        )
        rows = [
            [d.get("VarCharValue", "") for d in row["Data"]]
            for row in page["ResultSet"]["Rows"][1:]
        ]
        all_rows.extend(rows)
        next_token = page.get("NextToken")

    # 2) Générer le CSV en mémoire
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(cols)
    for row in all_rows:
        writer.writerow(row)
    buffer.seek(0)

    # 3) Retourner en StreamingResponse avec header pour téléchargement
    filename = f"{table}.csv"
    return StreamingResponse(
        buffer,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


@app.get("/api/tennis_paris", response_class=JSONResponse)
async def get_tennis_paris():
    athena = get_athena_client()
    sql = """
        SELECT
          inst_adresse AS adresse,
          inst_cp       AS cp,
          equip_x       AS lat,
          equip_y       AS lon,
          inst_nom AS "Nom installation sportive",
          equip_url AS url,
          equip_nature AS "Nature equipement",
          equip_prop_type AS "Type de propriété", 
          equip_gest_type AS "Type de gestion"
        FROM "france_equipement_complet"."processed_complet"
        WHERE equip_type_famille LIKE '%tennis%'
          AND dep_code = '75'
    """
    # 1) Lancer la requête
    exec_id = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": "france_equipement_complet"},
        ResultConfiguration={"OutputLocation": ATHENA_OUTPUT_PREVIEW}
    )["QueryExecutionId"]

    # 2) Attendre la fin
    while True:
        st = athena.get_query_execution(QueryExecutionId=exec_id)["QueryExecution"]["Status"]["State"]
        if st in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(0.5)
    if st != "SUCCEEDED":
        raise HTTPException(500, f"Athena query {st}")

    # 3) Récupérer tous les résultats (pagination)
    results = []
    next_token = None
    while True:
        params = {"QueryExecutionId": exec_id, "MaxResults": 1000}
        if next_token:
            params["NextToken"] = next_token
        resp = athena.get_query_results(**params)
        rows = resp["ResultSet"]["Rows"][1:]  # ignorer l’en-tête
        for row in rows:
            v = [d.get("VarCharValue", "") for d in row["Data"]]
        # attention aux index : v[2] = lat, v[3] = lon
            results.append({
            "adresse": v[0],
            "cp":      v[1],
            "lat":     v[2],
            "lon":     v[3],
            "Nom installation sportive":     v[4],
            "url":     v[5],
            "Nature equipement": v[6],
            "Type de propriété": v[7],
            "Type de gestion": v[8]
        })
        next_token = resp.get("NextToken")
        if not next_token:
            break

    return JSONResponse(content=results)

@app.get("/api/tennis_paris/count", response_class=JSONResponse)
async def count_tennis_paris():
    athena = get_athena_client()
    sql = """
        SELECT COUNT(*) AS total
        FROM "france_equipement_complet"."processed_complet"
        WHERE equip_type_famille LIKE '%tennis%'
          AND dep_code = '75'
    """
    # 1) Lancer la requête
    exec_id = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": "france_equipement_complet"},
        ResultConfiguration={"OutputLocation": ATHENA_OUTPUT_PREVIEW}
    )["QueryExecutionId"]

    # 2) Attendre la fin
    while True:
        st = athena.get_query_execution(QueryExecutionId=exec_id)["QueryExecution"]["Status"]["State"]
        if st in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(0.5)
    if st != "SUCCEEDED":
        raise HTTPException(500, f"Athena query {st}")

    # 3) Récupérer le résultat
    result = athena.get_query_results(QueryExecutionId=exec_id)
    count = int(result["ResultSet"]["Rows"][1]["Data"][0]["VarCharValue"])

    return JSONResponse(content={"count": count})

@app.get("/map", response_class=HTMLResponse)
async def tennis_map(request: Request):
    return templates.TemplateResponse("map.html", {"request": request})

@app.get("/api/tennis_paris/metrics", response_class=JSONResponse)
async def tennis_paris_metrics():
    athena = get_athena_client()
    db = "france_equipement_complet"
    table = "processed_complet"
    where = "equip_type_famille LIKE '%tennis%' AND dep_code='75'"

    # 1) Total, min/max service_date, avg size, %acc_libre, %saison
    sql1 = f"""
    SELECT
  COALESCE(new_name, 'Total') AS arrondissement,
  COUNT(*)                   AS total,
  MIN(equip_service_date)    AS oldest,
  MAX(equip_service_date)    AS newest,
  AVG(equip_long * equip_larg) AS avg_area
FROM "france_equipement_complet"."processed_complet"
WHERE equip_type_famille LIKE '%tennis%'
  AND dep_code = '75'
GROUP BY ROLLUP(new_name)
ORDER BY
  -- place la ligne « Total » en dernier
  CASE WHEN new_name IS NULL THEN 1 ELSE 0 END,
  -- puis trie les autres par total décroissant
  total DESC;
    """
    exec1 = athena.start_query_execution(
      QueryString=sql1,
      QueryExecutionContext={"Database": db},
      ResultConfiguration={"OutputLocation": ATHENA_OUTPUT_PREVIEW}
    )["QueryExecutionId"]
    # attendre…
    while True:
      st = athena.get_query_execution(QueryExecutionId=exec1)["QueryExecution"]["Status"]["State"]
      if st in ("SUCCEEDED","FAILED","CANCELLED"): break
      time.sleep(0.5)
    if st!="SUCCEEDED":
      raise HTTPException(500, f"Athena: {st}")

    # fetch result
    result_rows = athena.get_query_results(QueryExecutionId=exec1)["ResultSet"]["Rows"]
    headers = [col["VarCharValue"] for col in result_rows[0]["Data"]]
    data = []
    for r in result_rows[1:]:
        vals = [cell.get("VarCharValue") for cell in r["Data"]]
        obj = dict(zip(headers, vals))
        # conversion de types
        obj["total"]    = int(obj["total"])
        obj["avg_area"] = float(obj["avg_area"])
        data.append(obj)

    # 2) Répartition par surface
    sql2 = f"""
    SELECT equip_sol AS surface, COUNT(*) AS cnt
    FROM "{db}"."{table}"
    WHERE {where}
    GROUP BY equip_sol
    """
    exec2 = athena.start_query_execution(
      QueryString=sql2,
      QueryExecutionContext={"Database": db},
      ResultConfiguration={"OutputLocation": ATHENA_OUTPUT_PREVIEW}
    )["QueryExecutionId"]
    # attendre…
    while True:
      st = athena.get_query_execution(QueryExecutionId=exec2)["QueryExecution"]["Status"]["State"]
      if st in ("SUCCEEDED","FAILED","CANCELLED"): break
      time.sleep(0.5)
    if st!="SUCCEEDED":
      raise HTTPException(500, f"Athena: {st}")

    res2 = athena.get_query_results(QueryExecutionId=exec2)
    surf = {}
    for r in res2["ResultSet"]["Rows"][1:]:
      vals = [c.get("VarCharValue","") for c in r["Data"]]
      surf[vals[0]] = int(vals[1])

    # 3) Répartition par travaux (période)
    sql3 = f"""
    SELECT equip_travaux_periode AS periode, COUNT(*) AS cnt
    FROM "{db}"."{table}"
    WHERE {where}
    GROUP BY equip_travaux_periode
    """
    exec3 = athena.start_query_execution(
      QueryString=sql3,
      QueryExecutionContext={"Database": db},
      ResultConfiguration={"OutputLocation": ATHENA_OUTPUT_PREVIEW}
    )["QueryExecutionId"]
    # attendre…
    while True:
      st = athena.get_query_execution(QueryExecutionId=exec3)["QueryExecution"]["Status"]["State"]
      if st in ("SUCCEEDED","FAILED","CANCELLED"): break
      time.sleep(0.5)
    if st!="SUCCEEDED":
      raise HTTPException(500, f"Athena: {st}")

    res3 = athena.get_query_results(QueryExecutionId=exec3)

    travaux = {}

    for row in res3["ResultSet"]["Rows"][1:]:
        raw_periode = row["Data"][0].get("VarCharValue")
        raw_cnt     = row["Data"][1].get("VarCharValue") or "0"
        periode = raw_periode if raw_periode is not None else "Non renseigné"
        cnt     = int(raw_cnt)
        travaux[periode] = travaux.get(periode, 0)+cnt
      # 4) Récupérer % transport et % accessibilité par arrondissement et date de création
    sql4 = f"""
    SELECT
      COALESCE(new_name,'Total') AS arrondissement,
      ROUND(100.0*AVG(CASE WHEN inst_trans_bool   = 'true' THEN 1 ELSE 0 END),1) AS pct_with_transport,
     ROUND(100.0*AVG(CASE WHEN inst_acc_handi_bool = 'true' THEN 1 ELSE 0 END),1) AS pct_accessible_handicap,
       MIN(inst_date_creation)      AS first_creation,
      MAX(inst_date_creation)      AS last_creation
    FROM "{db}"."{table}"
    WHERE {where}
    GROUP BY ROLLUP(new_name)
    ORDER BY
      CASE WHEN new_name IS NULL THEN 1 ELSE 0 END,
      COUNT(*) DESC
    """
    exec4 = athena.start_query_execution(
      QueryString=sql4,
      QueryExecutionContext={"Database": db},
      ResultConfiguration={"OutputLocation": ATHENA_OUTPUT_PREVIEW}
    )["QueryExecutionId"]
    while True:
      st = athena.get_query_execution(QueryExecutionId=exec4)["QueryExecution"]["Status"]["State"]
      if st in ("SUCCEEDED","FAILED","CANCELLED"): break
      time.sleep(0.5)
    if st!="SUCCEEDED":
      raise HTTPException(500, f"Athena: {st}")

    # construire un dict de ces métriques
    res4 = athena.get_query_results(QueryExecutionId=exec4)
    transport_map = {}
    creation_by_year = []
    for r in res4["ResultSet"]["Rows"][1:]:
      cols = r["Data"]
      arr    = cols[0].get("VarCharValue") or "Total"
      pwt    = float(cols[1].get("VarCharValue","0"))
      pacc   = float(cols[2].get("VarCharValue","0"))
      transport_map[arr] = {
        "pct_with_transport": pwt,
        "pct_accessible_handicap": pacc
      }
      # on peut aussi ajouter les dates de création
      first_creation = cols[3].get("VarCharValue")
      last_creation = cols[4].get("VarCharValue")
      transport_map[arr].update({
        "first_creation": first_creation,
        "last_creation": last_creation
      })

    # fusionner dans chaque ligne de data[]
    for obj in data:
      key = obj.get("arrondissement") or "Total"
      if key in transport_map:
        obj.update(transport_map[key])
    # 5) Répartition par année de création
    # 5) Série temporelle — installations par année
    sql5 = f"""
    SELECT
    year(inst_date_etat) as year,
    count(*) as count
    FROM "france_equipement_complet"."processed_complet"
    WHERE equip_type_famille LIKE '%tennis%'
  AND dep_code = '75'
  GROUP BY 1
  ORDER BY 1
    """
    exec5 = athena.start_query_execution(
    QueryString=sql5,
    QueryExecutionContext={"Database": db},
    ResultConfiguration={"OutputLocation": ATHENA_OUTPUT_PREVIEW}
    )["QueryExecutionId"]

    # attendre comme d’habitude…
    while True:
        st = athena.get_query_execution(QueryExecutionId=exec5)["QueryExecution"]["Status"]["State"]
        if st in ("SUCCEEDED","FAILED","CANCELLED"):
            break
        time.sleep(0.5)
    if st != "SUCCEEDED":
        raise HTTPException(500, f"Athena: {st}")

    res5 = athena.get_query_results(QueryExecutionId=exec5)
    creation_by_year = []
    for row in res5["ResultSet"]["Rows"][1:]:
        cols = row["Data"]
        y = int(cols[0].get("VarCharValue","0"))
        c = int(cols[1].get("VarCharValue","0"))
        creation_by_year.append({"year": y, "count": c})

    #… vous pouvez faire de même pour inst_part_type, equip_utilisateur, inst_date_creation, etc.

    return JSONResponse({
        "data": data,
      "by_surface": surf,
      "by_travaux_period": travaux,
      "creation_by_year": creation_by_year
      # … d’autres séries
    })

@app.post("/api/feedback", response_class=JSONResponse)
async def feedback(payload: dict):
    # 1) Construire l’objet de feedback
    accepted = bool(payload.get("accepted", False))
    ts = datetime.utcnow().isoformat()
    record = {
        "timestamp": ts,
        "accepted": accepted
    }

    # 2) Écrire un fichier JSON dans S3
    bucket = "data-project-2025"
    prefix = "Api/results/"  # ← préfixe où Glue-Catalog regarde
    key = f"{prefix}{ts}.json"
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(record),
        ContentType="application/json"
    )

    # 3) Répondre au front
    return JSONResponse({"status": "ok"})


from mangum import Mangum
# Pour déployer sur AWS Lambda avec API Gateway
handler = Mangum(app)