import base64
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3

_lambda = boto3.client("lambda")
_dynamodb = boto3.resource("dynamodb")

ROLES_GESTION = {"admin", "despachador"}


class ErrorHttp(Exception):
    def __init__(self, status, mensaje):
        super().__init__(mensaje)
        self.status = status
        self.mensaje = mensaje


def tabla_repuestos():
    return _dynamodb.Table(os.environ["TABLA_REPUESTOS"])


def tabla_facturas():
    return _dynamodb.Table(os.environ["TABLA_FACTURAS"])


def ahora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def a_json(obj):
    if isinstance(obj, list):
        return [a_json(x) for x in obj]
    if isinstance(obj, dict):
        return {k: a_json(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj


def respuesta(status, body):
    return {"statusCode": status, "body": a_json(body)}


def validar_token(event):
    """Invoca la Lambda validar-token de MS-A y devuelve {tenant_id, rol, ...}."""
    headers = event.get("headers") or {}
    token = headers.get("Authorization") or headers.get("authorization") or ""
    if token.lower().startswith("bearer "):
        token = token[7:]
    if not token:
        raise ErrorHttp(403, "Falta el token")

    r = _lambda.invoke(
        FunctionName=os.environ["VALIDADOR_TOKEN"],
        InvocationType="RequestResponse",
        Payload=json.dumps({"token": token}),
    )
    datos = json.loads(r["Payload"].read())
    if datos.get("statusCode") != 200:
        raise ErrorHttp(403, "Token inválido o vencido")

    cuerpo_resp = datos.get("body") or {}
    if isinstance(cuerpo_resp, str):
        cuerpo_resp = json.loads(cuerpo_resp)
    if not cuerpo_resp.get("tenant_id"):
        raise ErrorHttp(403, "El token no trae tenant_id")
    return cuerpo_resp


def exigir_rol(usuario, permitidos):
    if usuario.get("rol") not in permitidos:
        raise ErrorHttp(403, "Tu rol no puede hacer esta operación")


def cuerpo(event):
    """Body de la petición; los números JSON se leen como Decimal (DynamoDB no acepta float)."""
    b = event.get("body") or {}
    if isinstance(b, str):
        return json.loads(b, parse_float=Decimal)
    return json.loads(json.dumps(b), parse_float=Decimal)


def path(event, nombre):
    return (event.get("path") or {}).get(nombre)


def query(event):
    return event.get("query") or {}


def limite(event, por_defecto=50, maximo=200):
    try:
        n = int(query(event).get("limite", por_defecto))
    except (TypeError, ValueError):
        raise ErrorHttp(400, "limite debe ser un entero")
    return max(1, min(n, maximo))


def codificar_paginacion(clave):
    return base64.urlsafe_b64encode(json.dumps(clave).encode()).decode() if clave else None


def decodificar_paginacion(texto):
    if not texto:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(texto.encode()))
    except Exception:  
        raise ErrorHttp(400, "El parámetro siguiente no es válido")


def manejar(funcion):
    def envoltura(event, context):
        try:
            usuario = validar_token(event)
            return funcion(event, usuario)
        except ErrorHttp as e:
            return respuesta(e.status, {"error": e.mensaje})
        except Exception as e: 
            print("ERROR", repr(e))
            return respuesta(500, {"error": "Error interno"})
    return envoltura
