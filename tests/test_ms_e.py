import importlib
import io
import json
import os
import sys
from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x",
                  TABLA_REPUESTOS="fsm-test-repuestos", TABLA_FACTURAS="fsm-test-facturas",
                  BUCKET_FACTURAS="fsm-test-facturas-bucket", VALIDADOR_TOKEN="fsm-test-validar-token",
                  TARIFA_HORA="50")

TOKENS = {"tok-admin-a": {"tenant_id": "tenant-a", "rol": "admin"},
          "tok-tec-a": {"tenant_id": "tenant-a", "rol": "tecnico"},
          "tok-admin-b": {"tenant_id": "tenant-b", "rol": "admin"}}


class FalsoLambda:
    def invoke(self, FunctionName, InvocationType, Payload):
        token = json.loads(Payload)["token"]
        r = {"statusCode": 200, "body": TOKENS[token]} if token in TOKENS else {"statusCode": 403, "body": "x"}
        return {"Payload": io.BytesIO(json.dumps(r).encode())}


def _crear_tabla(nombre, sk):
    boto3.client("dynamodb").create_table(
        TableName=nombre, BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "tenant_id", "AttributeType": "S"},
                              {"AttributeName": sk, "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "tenant_id", "KeyType": "HASH"},
                   {"AttributeName": sk, "KeyType": "RANGE"}])


@pytest.fixture
def aws():
    with mock_aws():
        _crear_tabla("fsm-test-repuestos", "repuesto_id")
        _crear_tabla("fsm-test-facturas", "factura_id")
        boto3.client("s3").create_bucket(Bucket="fsm-test-facturas-bucket")
        from src import comun, eventos, facturas, repuestos
        for m in (comun, repuestos, facturas, eventos):
            importlib.reload(m)
        comun._lambda = FalsoLambda()
        yield {"repuestos": repuestos, "facturas": facturas, "eventos": eventos,
               "s3": boto3.client("s3"), "ddb": boto3.resource("dynamodb")}


def ev(token, body=None, path=None, query=None):
    return {"headers": {"Authorization": f"Bearer {token}"}, "body": body or {}, "path": path or {},
            "query": query or {}}


def sembrar(aws, tenant, repuesto_id, precio, nombre="Filtro"):
    aws["ddb"].Table("fsm-test-repuestos").put_item(Item={
        "tenant_id": tenant, "repuesto_id": repuesto_id, "nombre": nombre,
        "precio_unitario": Decimal(str(precio)), "stock": 10})


def mensaje_sqs(evento, tenant, cuerpo, envuelto):
    if envuelto:  
        body = {"Type": "Notification", "Message": json.dumps(cuerpo),
                "MessageAttributes": {"evento": {"Type": "String", "Value": evento},
                                      "tenant_id": {"Type": "String", "Value": tenant}}}
        return {"messageId": "m1", "body": json.dumps(body)}
    return {"messageId": "m1", "body": json.dumps(cuerpo),
            "messageAttributes": {"evento": {"stringValue": evento, "dataType": "String"},
                                  "tenant_id": {"stringValue": tenant, "dataType": "String"}}}


VISITA = {"tenant_id": "tenant-a", "visita_id": "VIS-1", "orden_id": "ORD-1", "cliente_id": "CLI-1",
          "sitio_id": "SIT-1", "tecnico_id": "TEC-1", "duracion_horas": 2.0,
          "repuestos_usados": [{"repuesto_id": "REP-0001", "cantidad": 3},
                               {"repuesto_id": "REP-0002", "cantidad": 1}],
          "completada_en": "2026-11-10T16:00:00Z"}


def test_token_invalido_y_rol(aws):
    R = aws["repuestos"]
    assert R.listar(ev("malo"), None)["statusCode"] == 403
    assert R.crear(ev("tok-tec-a", {"nombre": "X", "precio_unitario": 5}), None)["statusCode"] == 403


def test_crud_repuestos_y_aislamiento_por_tenant(aws):
    R = aws["repuestos"]
    r = R.crear(ev("tok-admin-a", {"nombre": "Filtro", "precio_unitario": 12.5, "stock": 4}), None)
    assert r["statusCode"] == 201 and r["body"]["precio_unitario"] == 12.5
    rid = r["body"]["repuesto_id"]

    assert R.crear(ev("tok-admin-a", {"nombre": "Sin precio"}), None)["statusCode"] == 400
    assert R.crear(ev("tok-admin-a", {"nombre": "Neg", "precio_unitario": -1}), None)["statusCode"] == 400

    assert R.obtener(ev("tok-tec-a", path={"repuesto_id": rid}), None)["body"]["nombre"] == "Filtro"
    assert R.obtener(ev("tok-admin-b", path={"repuesto_id": rid}), None)["statusCode"] == 404
    assert R.listar(ev("tok-admin-b"), None)["body"]["items"] == []
    assert len(R.listar(ev("tok-admin-a"), None)["body"]["items"]) == 1

    r = R.actualizar(ev("tok-admin-a", {"precio_unitario": 15, "stock": 9}, {"repuesto_id": rid}), None)
    assert r["statusCode"] == 200 and r["body"]["precio_unitario"] == 15 and r["body"]["stock"] == 9
    assert R.actualizar(ev("tok-admin-a", {"stock": 1}, {"repuesto_id": "REP-NO"}), None)["statusCode"] == 404

    assert R.eliminar(ev("tok-admin-a", path={"repuesto_id": rid}), None)["statusCode"] == 200
    assert R.eliminar(ev("tok-admin-a", path={"repuesto_id": rid}), None)["statusCode"] == 404


def test_paginacion_repuestos(aws):
    R = aws["repuestos"]
    for i in range(5):
        sembrar(aws, "tenant-a", f"REP-000{i}", 10)
    p1 = R.listar(ev("tok-admin-a", query={"limite": "2"}), None)["body"]
    assert len(p1["items"]) == 2 and p1["siguiente"]
    p2 = R.listar(ev("tok-admin-a", query={"limite": "10", "siguiente": p1["siguiente"]}), None)["body"]
    assert len(p2["items"]) == 3 and p2["siguiente"] is None


@pytest.mark.parametrize("envuelto", [True, False])
def test_generar_factura(aws, envuelto):
    sembrar(aws, "tenant-a", "REP-0001", 20, "Filtro")
    sembrar(aws, "tenant-a", "REP-0002", 30.5, "Valvula")
    res = aws["eventos"].generar_factura(
        {"Records": [mensaje_sqs("visita.completada", "tenant-a", VISITA, envuelto)]}, None)
    assert res == {"creadas": 1, "repetidas": 0, "ignoradas": 0}

    f = aws["ddb"].Table("fsm-test-facturas").get_item(
        Key={"tenant_id": "tenant-a", "factura_id": "FAC-VIS-1"})["Item"]
    assert f["subtotal"] == Decimal("190.50")
    assert f["igv"] == Decimal("34.29")
    assert f["total"] == Decimal("224.79")
    assert f["estado"] == "emitida" and len(f["items"]) == 2

    pdf = aws["s3"].get_object(Bucket="fsm-test-facturas-bucket", Key="tenant-a/facturas/FAC-VIS-1.pdf")["Body"].read()
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    assert f["pdf_key"] == "tenant-a/facturas/FAC-VIS-1.pdf"


def test_mensaje_duplicado_no_duplica(aws):
    sembrar(aws, "tenant-a", "REP-0001", 20)
    sembrar(aws, "tenant-a", "REP-0002", 30)
    rec = mensaje_sqs("visita.completada", "tenant-a", VISITA, True)
    assert aws["eventos"].generar_factura({"Records": [rec]}, None)["creadas"] == 1
    assert aws["eventos"].generar_factura({"Records": [rec]}, None) == {"creadas": 0, "repetidas": 1, "ignoradas": 0}
    assert aws["ddb"].Table("fsm-test-facturas").scan()["Count"] == 1


def test_evento_ajeno_se_ignora_y_repuesto_desconocido_avisa(aws):
    otro = mensaje_sqs("orden.creada", "tenant-a", {"orden_id": "ORD-9"}, False)
    assert aws["eventos"].generar_factura({"Records": [otro]}, None)["ignoradas"] == 1

    aws["eventos"].generar_factura({"Records": [mensaje_sqs("visita.completada", "tenant-a", VISITA, False)]}, None)
    f = aws["ddb"].Table("fsm-test-facturas").get_item(
        Key={"tenant_id": "tenant-a", "factura_id": "FAC-VIS-1"})["Item"]
    assert len(f["advertencias"]) == 2 and f["subtotal"] == Decimal("100.00")  


def test_api_facturas_por_tenant_y_pdf(aws):
    sembrar(aws, "tenant-a", "REP-0001", 20)
    sembrar(aws, "tenant-a", "REP-0002", 30)
    aws["eventos"].generar_factura({"Records": [mensaje_sqs("visita.completada", "tenant-a", VISITA, True)]}, None)
    F = aws["facturas"]

    lista = F.listar(ev("tok-tec-a"), None)["body"]["items"]
    assert [x["factura_id"] for x in lista] == ["FAC-VIS-1"]
    assert F.listar(ev("tok-admin-b"), None)["body"]["items"] == []
    assert F.obtener(ev("tok-admin-b", path={"factura_id": "FAC-VIS-1"}), None)["statusCode"] == 404

    r = F.pdf(ev("tok-tec-a", path={"factura_id": "FAC-VIS-1"}), None)
    assert r["statusCode"] == 200 and "FAC-VIS-1.pdf" in r["body"]["url"]
    assert F.listar(ev("tok-tec-a", query={"estado": "pagada"}), None)["body"]["items"] == []
