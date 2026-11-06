import os

import boto3
from boto3.dynamodb.conditions import Attr, Key

from src.comun import (ErrorHttp, codificar_paginacion, decodificar_paginacion, limite, manejar,
                       path, query, respuesta, tabla_facturas)

_s3 = boto3.client("s3")


@manejar
def listar(event, usuario):
    q = query(event)
    args = {
        "KeyConditionExpression": Key("tenant_id").eq(usuario["tenant_id"]),
        "Limit": limite(event),
    }
    inicio = decodificar_paginacion(q.get("siguiente"))
    if inicio:
        args["ExclusiveStartKey"] = inicio
    if q.get("estado"):
        args["FilterExpression"] = Attr("estado").eq(q["estado"])
    r = tabla_facturas().query(**args)
    return respuesta(200, {"items": r["Items"], "siguiente": codificar_paginacion(r.get("LastEvaluatedKey"))})


def _buscar(usuario, factura_id):
    r = tabla_facturas().get_item(Key={"tenant_id": usuario["tenant_id"], "factura_id": factura_id})
    if "Item" not in r:
        raise ErrorHttp(404, "Factura no encontrada")
    return r["Item"]


@manejar
def obtener(event, usuario):
    return respuesta(200, _buscar(usuario, path(event, "factura_id")))


@manejar
def pdf(event, usuario):
    f = _buscar(usuario, path(event, "factura_id"))
    if not f.get("pdf_key"):
        raise ErrorHttp(404, "Esta factura no tiene PDF")
    url = _s3.generate_presigned_url(
        "get_object", Params={"Bucket": os.environ["BUCKET_FACTURAS"], "Key": f["pdf_key"]},
        ExpiresIn=900)
    return respuesta(200, {"factura_id": f["factura_id"], "url": url, "expira_en_segundos": 900})
