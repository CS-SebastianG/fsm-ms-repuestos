import argparse
import json
import random
from datetime import datetime, timedelta
from decimal import Decimal

CATEGORIAS = ["eléctrico", "mecánico", "hidráulico", "electrónico"]
PIEZAS = ["Filtro", "Válvula", "Sensor", "Correa", "Fusible", "Interruptor", "Cable", "Bomba", "Rodamiento", "Termostato"]
ESTADOS = ["emitida", "pagada", "pendiente"]
BASE = datetime(2026, 9, 1, 8, 0, 0)


def codigo(prefijo, n, ancho):
    return f"{prefijo}-{n:0{ancho}d}"


def precio_repuesto(n):
    return Decimal(20 + (n % 60) * 4)


def fecha(n):
    return (BASE + timedelta(hours=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def generar_repuesto(tenant, n):
    return {
        "tenant_id": tenant,
        "repuesto_id": codigo("REP", n, 4),
        "nombre": f"{PIEZAS[n % len(PIEZAS)]} {n}",
        "categoria": CATEGORIAS[n % len(CATEGORIAS)],
        "precio_unitario": precio_repuesto(n),
        "stock": n % 80,
        "actualizado_en": fecha(n),
    }


def generar_factura(tenant, n, rnd, max_repuesto=500):
    items = []
    for _ in range(rnd.randint(1, 3)):
        rep = rnd.randint(1, max_repuesto)
        cant = rnd.randint(1, 4)
        precio = precio_repuesto(rep)
        items.append({"repuesto_id": codigo("REP", rep, 4), "nombre": f"{PIEZAS[rep % len(PIEZAS)]} {rep}",
                      "cantidad": cant, "precio_unitario": precio, "subtotal": precio * cant})
    horas = Decimal(rnd.choice([1, 2, 3, 4]))
    mano = {"horas": horas, "tarifa_hora": Decimal(50), "subtotal": horas * 50}
    subtotal = sum((i["subtotal"] for i in items), Decimal(0)) + mano["subtotal"]
    igv = (subtotal * Decimal("0.18")).quantize(Decimal("0.01"))
    return {
        "tenant_id": tenant,
        "factura_id": codigo("FAC", n, 6),
        "visita_id": codigo("VIS", n, 6),
        "orden_id": codigo("ORD", n, 6),
        "cliente_id": codigo("CLI", rnd.randint(1, 500), 4),
        "sitio_id": codigo("SIT", rnd.randint(1, 500), 4),
        "tecnico_id": codigo("TEC", rnd.randint(1, 50), 3),
        "items": items,
        "mano_obra": mano,
        "subtotal": subtotal,
        "igv": igv,
        "total": subtotal + igv,
        "estado": ESTADOS[n % 3],
        "fecha_emision": fecha(n),
    }


def registros(tenants, n_repuestos, n_facturas):
    rnd = random.Random(42)
    for t in tenants:
        for n in range(1, n_repuestos + 1):
            yield "repuestos", generar_repuesto(t, n)
        for n in range(1, n_facturas + 1):
            yield "facturas", generar_factura(t, n, rnd)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stage", default="dev")
    p.add_argument("--tenants", default="tenant-1,tenant-2,tenant-3")
    p.add_argument("--repuestos", type=int, default=5000, help="repuestos por tenant")
    p.add_argument("--facturas", type=int, default=5000, help="facturas por tenant")
    p.add_argument("--dry-run", action="store_true", help="no escribe en AWS; muestra un ejemplo de cada tabla")
    a = p.parse_args()
    tenants = [t.strip() for t in a.tenants.split(",") if t.strip()]

    if a.dry_run:
        mostrados, total = set(), 0
        for tabla, item in registros(tenants, a.repuestos, a.facturas):
            total += 1
            if tabla not in mostrados:
                mostrados.add(tabla)
                print(f"Ejemplo de {tabla}:\n", json.dumps(item, default=str, ensure_ascii=False, indent=2))
        print(f"Total de registros que se cargarían: {total}")
        return

    import boto3  
    ddb = boto3.resource("dynamodb")
    tablas = {"repuestos": ddb.Table(f"fsm-{a.stage}-repuestos"), "facturas": ddb.Table(f"fsm-{a.stage}-facturas")}
    escritores = {k: t.batch_writer(overwrite_by_pkeys=["tenant_id", "repuesto_id" if k == "repuestos" else "factura_id"])
                  for k, t in tablas.items()}
    for w in escritores.values():
        w.__enter__()
    total = 0
    try:
        for tabla, item in registros(tenants, a.repuestos, a.facturas):
            escritores[tabla].put_item(Item=item)
            total += 1
            if total % 2000 == 0:
                print(f"  {total} registros...")
    finally:
        for w in escritores.values():
            w.__exit__(None, None, None)
    print(f"Listo: {total} registros cargados en fsm-{a.stage}-repuestos y fsm-{a.stage}-facturas")


if __name__ == "__main__":
    main()
