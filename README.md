# MS-E · Repuestos y Facturación (FSM SaaS)

Lleva el catálogo de repuestos de cada empresa (tenant) y genera la factura de cada visita completada: la guarda en DynamoDB y su PDF en S3.

**Stack:** Python 3.12 · API Gateway (`integration: lambda`) · Lambda · DynamoDB · S3 · SQS · Serverless Framework v3 · Learner Lab (LabRole)

## Flujo de eventos

```
MS-D completa una visita ──► SNS fsm-<stage>-eventos ──filtro evento=visita.completada──► SQS fsm-<stage>-cola-facturacion
                                                                                             │ (dispara)
                                                                                             ▼
                                                               Lambda generarFactura ──► DynamoDB facturas + S3 <tenant_id>/facturas/<factura_id>.pdf
```

## Endpoints

Todos exigen `Authorization: Bearer <token>`. Cada Lambda invoca `fsm-<stage>-validar-token`, que devuelve `tenant_id` y `rol`; solo se opera sobre los datos de ese tenant.

| Método | Ruta | Qué hace | Rol |
|---|---|---|---|
| POST | `/repuestos` | Crea un repuesto (`nombre`, `precio_unitario`, opcional `categoria`, `stock`) | admin, despachador |
| GET | `/repuestos?categoria=&limite=&siguiente=` | Lista paginada del tenant | todos |
| GET | `/repuestos/{repuesto_id}` | Detalle | todos |
| PUT | `/repuestos/{repuesto_id}` | Edita nombre, categoría, precio o stock | admin, despachador |
| DELETE | `/repuestos/{repuesto_id}` | Elimina | admin, despachador |
| GET | `/facturas?estado=&limite=&siguiente=` | Lista paginada del tenant | todos |
| GET | `/facturas/{factura_id}` | Detalle | todos |
| GET | `/facturas/{factura_id}/pdf` | URL firmada (15 min) para descargar el PDF | todos |

Las respuestas siguen el formato de los talleres: `{"statusCode": ..., "body": ...}`.

## Eventos

- **Consume `visita.completada`** desde `cola-facturacion` (publicado por MS-D con los atributos `evento` y `tenant_id`). Lee el mensaje SNS envuelto (Body → Message) y también el raw message delivery.
- Crea la factura `FAC-<visita_id>`. Si SQS entrega el mismo mensaje dos veces, no la duplica.
- Si un repuesto de la visita no está en el catálogo, la factura se emite igual con ese ítem en 0 y un aviso en `advertencias`.
- **Mano de obra:** `duracion_horas × TARIFA_HORA` (variable de entorno en `serverless.yml`, por defecto 50; con 0 no se cobra).
- **IGV:** 18 % sobre el subtotal.

## Datos (DynamoDB)

**`fsm-<stage>-repuestos`** · PK `tenant_id` · SK `repuesto_id`

```json
{"tenant_id": "tenant-1", "repuesto_id": "REP-0001", "nombre": "Válvula 1", "categoria": "mecánico",
 "precio_unitario": 24, "stock": 1, "actualizado_en": "2026-09-01T09:00:00Z"}
```

**`fsm-<stage>-facturas`** · PK `tenant_id` · SK `factura_id`

```json
{"tenant_id": "tenant-1", "factura_id": "FAC-VIS-000001", "visita_id": "VIS-000001", "orden_id": "ORD-000001",
 "cliente_id": "CLI-0378", "sitio_id": "SIT-0053", "tecnico_id": "TEC-044",
 "items": [{"repuesto_id": "REP-0058", "nombre": "Rodamiento 58", "cantidad": 1, "precio_unitario": 252, "subtotal": 252}],
 "mano_obra": {"horas": 2, "tarifa_hora": 50, "subtotal": 100},
 "subtotal": 352, "igv": 63.36, "total": 415.36, "estado": "emitida",
 "fecha_emision": "2026-09-01T09:00:00Z", "pdf_key": "tenant-1/facturas/FAC-VIS-000001.pdf"}
```

**S3 `fsm-<stage>-facturas-<cuenta>`:** guarda los PDF bajo `<tenant_id>/facturas/`.

> Los repuestos `REP-0001` a `REP-0500` existen en todos los tenants porque MS-D los usa en sus visitas.

## Desplegar y cargar datos

```bash
npm i -g serverless@3
sls deploy --stage dev        # antes deben existir MS-A (validar-token) y MS-C (topic y colas)
export AWS_DEFAULT_REGION=us-east-1
python3 scripts/poblar_repuestos.py --stage dev    # 30 000 registros (5 000 repuestos + 5 000 facturas por tenant)
```

Para ver ejemplos sin tocar AWS: `python3 scripts/poblar_repuestos.py --dry-run`

## Pruebas locales (AWS simulado)

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Estado

- Desplegado y probado de punta a punta junto con los demás microservicios: login → orden → visita → completar visita → factura automática con su PDF.
- Aislamiento por tenant comprobado: un tenant no ve ni modifica los datos de otro.
- Contratos confirmados con MS-A (`validar-token` recibe `{"token": ...}` y responde `{"statusCode": 200, "body": {"tenant_id", "user_id", "rol"}}`) y con MS-C/MS-D (suscripción de `cola-facturacion` y políticas de las colas).
