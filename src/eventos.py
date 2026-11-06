import json
import os
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

from src.comun import ahora, tabla_facturas, tabla_repuestos
from src.pdf import crear_pdf

_s3 = boto3.client("s3")
IGV = Decimal("0.18")


def _dinero(valor):
    return Decimal(str(valor)).quantize(Decimal("0.01"))


def _leer_mensaje(record):
    body = json.loads(record["body"])
    if body.get("Type") == "Notification" and "Message" in body:
        msg = json.loads(body["Message"])
        attrs = {k: v.get("Value") for k, v in (body.get("MessageAttributes") or {}).items()}
    else:
        msg = body
        attrs = {k: v.get("stringValue") for k, v in (record.get("messageAttributes") or {}).items()}
    evento = attrs.get("evento") or msg.get("evento")
    tenant_id = attrs.get("tenant_id") or msg.get("tenant_id")
    return evento, tenant_id, msg


def _armar_factura(tenant_id, visita):
    items, advertencias = [], []
    for uso in visita.get("repuestos_usados") or []:
        repuesto_id = uso.get("repuesto_id")
        cantidad = int(uso.get("cantidad", 1))
        rep = tabla_repuestos().get_item(
            Key={"tenant_id": tenant_id, "repuesto_id": repuesto_id}).get("Item")
        if rep is None:
            advertencias.append(f"Repuesto {repuesto_id} no está en el catálogo")
            precio, nombre = Decimal("0"), repuesto_id
        else:
            precio, nombre = Decimal(str(rep["precio_unitario"])), rep.get("nombre", repuesto_id)
        items.append({"repuesto_id": repuesto_id, "nombre": nombre, "cantidad": cantidad,
                      "precio_unitario": _dinero(precio), "subtotal": _dinero(precio * cantidad)})

    mano_obra = None
    horas = visita.get("duracion_horas")
    tarifa = Decimal(os.environ.get("TARIFA_HORA", "0"))
    if horas and tarifa > 0:
        horas = Decimal(str(horas))
        mano_obra = {"horas": horas, "tarifa_hora": _dinero(tarifa), "subtotal": _dinero(horas * tarifa)}

    subtotal = sum((i["subtotal"] for i in items), Decimal("0.00"))
    if mano_obra:
        subtotal += mano_obra["subtotal"]
    igv = _dinero(subtotal * IGV)

    visita_id = visita["visita_id"]
    factura = {
        "tenant_id": tenant_id,
        "factura_id": f"FAC-{visita_id}",
        "visita_id": visita_id,
        "orden_id": visita.get("orden_id", ""),
        "cliente_id": visita.get("cliente_id", ""),
        "sitio_id": visita.get("sitio_id", ""),
        "tecnico_id": visita.get("tecnico_id") or "",
        "items": items,
        "subtotal": subtotal,
        "igv": igv,
        "total": subtotal + igv,
        "estado": "emitida",
        "fecha_emision": ahora(),
    }
    if mano_obra:
        factura["mano_obra"] = mano_obra
    if advertencias:
        factura["advertencias"] = advertencias
    return factura


def _lineas_pdf(f):
    lineas = [
        "FACTURA " + f["factura_id"], "",
        f"Empresa (tenant): {f['tenant_id']}",
        f"Fecha de emision: {f['fecha_emision']}",
        f"Cliente: {f['cliente_id']}    Sitio: {f['sitio_id']}",
        f"Orden: {f['orden_id']}    Visita: {f['visita_id']}    Tecnico: {f['tecnico_id']}", "",
        "Detalle", "-" * 70,
    ]
    for i in f["items"][:30]:
        lineas.append(f"{i['cantidad']} x {i['nombre']} ({i['repuesto_id']})  "
                      f"P.U. {i['precio_unitario']:.2f}  Subtotal {i['subtotal']:.2f}")
    if len(f["items"]) > 30:
        lineas.append(f"... y {len(f['items']) - 30} items mas")
    if f.get("mano_obra"):
        m = f["mano_obra"]
        lineas.append(f"Mano de obra: {m['horas']} h x {m['tarifa_hora']:.2f}  Subtotal {m['subtotal']:.2f}")
    lineas += ["-" * 70, f"Subtotal: {f['subtotal']:.2f}", f"IGV (18%): {f['igv']:.2f}",
               f"TOTAL: {f['total']:.2f}"]
    for a in f.get("advertencias", []):
        lineas.append("Aviso: " + a)
    return lineas


def generar_factura(event, context):
    creadas = repetidas = ignoradas = 0
    for record in event.get("Records", []):
        evento, tenant_id, visita = _leer_mensaje(record)
        if evento != "visita.completada" or not tenant_id or not visita.get("visita_id"):
            print("Mensaje ignorado:", record.get("messageId"), evento)
            ignoradas += 1
            continue

        factura_id = f"FAC-{visita['visita_id']}"
        ya = tabla_facturas().get_item(Key={"tenant_id": tenant_id, "factura_id": factura_id})
        if "Item" in ya:
            repetidas += 1
            continue

        factura = _armar_factura(tenant_id, visita)
        factura["pdf_key"] = f"{tenant_id}/facturas/{factura_id}.pdf"
        _s3.put_object(Bucket=os.environ["BUCKET_FACTURAS"], Key=factura["pdf_key"],
                       Body=crear_pdf(_lineas_pdf(factura)), ContentType="application/pdf")
        try:
            tabla_facturas().put_item(Item=factura, ConditionExpression="attribute_not_exists(factura_id)")
            creadas += 1
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise  
            repetidas += 1
    print(json.dumps({"creadas": creadas, "repetidas": repetidas, "ignoradas": ignoradas}))
    return {"creadas": creadas, "repetidas": repetidas, "ignoradas": ignoradas}
