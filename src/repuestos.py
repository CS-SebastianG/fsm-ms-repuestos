import uuid
from decimal import Decimal, InvalidOperation

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from src.comun import (ROLES_GESTION, ErrorHttp, ahora, codificar_paginacion, cuerpo,
                       decodificar_paginacion, exigir_rol, limite, manejar, path, query,
                       respuesta, tabla_repuestos)

CAMPOS_EDITABLES = ("nombre", "categoria", "precio_unitario", "stock")


def _numero(valor, campo, entero=False):
    try:
        n = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        raise ErrorHttp(400, f"{campo} debe ser numérico")
    if n < 0:
        raise ErrorHttp(400, f"{campo} no puede ser negativo")
    if entero:
        if n % 1 != 0:
            raise ErrorHttp(400, f"{campo} debe ser entero")
        return int(n)
    return n


def _validar(campo, valor):
    if campo == "precio_unitario":
        return _numero(valor, campo)
    if campo == "stock":
        return _numero(valor, campo, entero=True)
    if not str(valor).strip():
        raise ErrorHttp(400, f"{campo} no puede estar vacío")
    return str(valor).strip()


@manejar
def crear(event, usuario):
    exigir_rol(usuario, ROLES_GESTION)
    b = cuerpo(event)
    for campo in ("nombre", "precio_unitario"):
        if b.get(campo) in (None, ""):
            raise ErrorHttp(400, f"Falta el campo {campo}")
    item = {
        "tenant_id": usuario["tenant_id"],
        "repuesto_id": "REP-" + uuid.uuid4().hex[:8].upper(),
        "nombre": _validar("nombre", b["nombre"]),
        "categoria": _validar("categoria", b.get("categoria") or "general"),
        "precio_unitario": _validar("precio_unitario", b["precio_unitario"]),
        "stock": _validar("stock", b.get("stock", 0)),
        "actualizado_en": ahora(),
    }
    tabla_repuestos().put_item(Item=item, ConditionExpression="attribute_not_exists(repuesto_id)")
    return respuesta(201, item)


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
    if q.get("categoria"):
        args["FilterExpression"] = Attr("categoria").eq(q["categoria"])
    r = tabla_repuestos().query(**args)
    return respuesta(200, {"items": r["Items"], "siguiente": codificar_paginacion(r.get("LastEvaluatedKey"))})


@manejar
def obtener(event, usuario):
    r = tabla_repuestos().get_item(
        Key={"tenant_id": usuario["tenant_id"], "repuesto_id": path(event, "repuesto_id")})
    if "Item" not in r:
        raise ErrorHttp(404, "Repuesto no encontrado")
    return respuesta(200, r["Item"])


@manejar
def actualizar(event, usuario):
    exigir_rol(usuario, ROLES_GESTION)
    b = cuerpo(event)
    cambios = {c: _validar(c, b[c]) for c in CAMPOS_EDITABLES if c in b}
    if not cambios:
        raise ErrorHttp(400, "No hay campos para actualizar")
    cambios["actualizado_en"] = ahora()
    nombres = {f"#{c}": c for c in cambios}
    valores = {f":{c}": v for c, v in cambios.items()}
    expr = "SET " + ", ".join(f"#{c} = :{c}" for c in cambios)
    try:
        r = tabla_repuestos().update_item(
            Key={"tenant_id": usuario["tenant_id"], "repuesto_id": path(event, "repuesto_id")},
            UpdateExpression=expr,
            ConditionExpression="attribute_exists(repuesto_id)",
            ExpressionAttributeNames=nombres,
            ExpressionAttributeValues=valores,
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ErrorHttp(404, "Repuesto no encontrado")
        raise
    return respuesta(200, r["Attributes"])


@manejar
def eliminar(event, usuario):
    exigir_rol(usuario, ROLES_GESTION)
    try:
        tabla_repuestos().delete_item(
            Key={"tenant_id": usuario["tenant_id"], "repuesto_id": path(event, "repuesto_id")},
            ConditionExpression="attribute_exists(repuesto_id)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ErrorHttp(404, "Repuesto no encontrado")
        raise
    return respuesta(200, {"mensaje": "Repuesto eliminado"})
