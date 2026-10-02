# FASE 1.14 — Integración operativa de disponibilidad en Binance Spot REST

## Estado

FASE 1.14 implementada sobre la continuidad exacta de FASE 1.13.

HEAD inicial:

`63386bd767f1bb06108aaa47e3547c04e6851fef`

La integración no modifica `bot_obrero/market_data.py`, `bot_obrero/temporal.py` ni la documentación histórica de FASE 1.12.

---

## 1. Frontera de consumidor

La frontera de disponibilidad del método:

`BinanceSpotRestAdapter.fetch_market_data()`

es una **frontera pre-return dentro del adapter**.

El `consumer_handoff_clock` no observa literalmente el instante en que el caller recibe el valor retornado. El callback se ejecuta dentro de `fetch_market_data()`, después de completar la construcción y validación canónica de todos los `MarketData` y antes del `return`.

Por tanto, la frontera representa:

> el instante medido dentro del adapter en el que el resultado canónico está completamente preparado y el adapter está a punto de entregarlo mediante el retorno de la función.

El `consumer_scope` utilizado por `AvailabilityEvidence` es:

`BinanceSpotRestAdapter.fetch_market_data pre-return availability boundary`

La semántica no afirma que el timestamp sea el instante posterior observable por el caller. Entre el timestamp y la observación externa del retorno existe necesariamente la operación de retorno de la función.

No se crea una cola, evento, worker ni una capa de eventos nueva.

La frontera solo se activa cuando se inyecta explícitamente `consumer_handoff_clock`.

Sin ese clock, la frontera no se declara demostrada y `available_at` permanece `None`.

---

## 2. Timestamps T1–T5

### T1 — respuesta HTTP completamente recibida

`received_at` se captura inmediatamente después de que `_request()` termina de leer la respuesta HTTP completa.

En términos operativos:

```
T1 = received_at
```

Esto no implica availability.

### T2 — parsing terminado

`_provider_records()` interpreta la respuesta Binance, valida la forma de cada kline y construye las representaciones de proveedor.

```
T2 = parsing completo
```

No se usa T2 como `available_at`.

### T3 — normalización terminada

Cada registro Binance pasa por:

`parse_provider_payload()`

y:

`normalize_provider_record()`

```
T3 = normalización completa
```

T3 tampoco se usa automáticamente como availability.

### T4 — canonical validation terminada

Cada elemento pasa por:

`canonicalize_market_data()`

y solo si la validación canónica completa correctamente existe un `MarketData` canónico.

```
T4 = MarketData canónico construido y validado
```

La prueba de FASE 1.14 demuestra además que el reloj de handoff **no se ejecuta** cuando la validación canónica falla.

### T5 — pre-return availability boundary

Después de que toda la lista ha sido canonicalizada, y justo antes de devolverla, el adapter consulta el:

`consumer_handoff_clock`

Cuando está configurado:

```
T5 = consumer_handoff_clock()
```

T5 **no** es una observación literal del momento en que el caller recibe el objeto. Es la última frontera temporal que el adapter mide explícitamente antes del `return`.

Ese timestamp se convierte en `available_at` mediante:

`AvailabilityEvidence.consumer_handoff(...)`

La implementación no intenta medir el instante de scheduling del sistema operativo ni inventa una precisión inferior a la que proporciona el reloj inyectado. T5 representa la frontera explícita de entrega local que la arquitectura declara medible.

---

## 3. Regla de availability

El adapter tiene dos estados deliberados.

### UNKNOWN

Configuración por defecto:

```python
consumer_handoff_clock=None
```

Resultado:

```
available_at = None
```

No existe fallback a:

- `received_at`;
- `observed_at`;
- open time;
- close time;
- request time.

### CONSUMER_HANDOFF

Cuando se configura un reloj de handoff:

```
AvailabilityEvidence.consumer_handoff(
    received_at=received_at,
    available_at=T5,
    evidence_reference="binance-spot-rest.fetch_market_data:pre-return-boundary",
    consumer_scope="BinanceSpotRestAdapter.fetch_market_data pre-return availability boundary",
)
```

Entonces:

```
available_at = T5
```

siempre que:

```
T5 >= received_at
```

La implementación usa la abstracción de FASE 1.13 para resolver esta evidencia y no implementa una segunda regla temporal.

---

## 4. Por qué no se usa RECEIVED_AND_AVAILABLE

Binance no declara que el instante de recepción HTTP sea automáticamente el instante en que el consumidor del `MarketData` puede acceder al objeto canónico.

Por eso FASE 1.14 no convierte:

```
received_at == available_at
```

por defecto.

La ruta explícita de Binance es únicamente:

```
CONSUMER_HANDOFF
```

cuando existe una frontera de consumidor declarada y medible.

Sin esa frontera se conserva:

```
UNKNOWN
```

---

## 5. Observed time y close time

La semántica existente permanece:

```
observed_at = Binance kline open time
```

Ni `observed_at` ni `close_time` se utilizan para calcular disponibilidad.

Una vela cerrada no se considera automáticamente disponible desde su close time.

Una vela histórica no se considera automáticamente conocida desde su open time.

---

## 6. External evidence

Binance **no utiliza**:

```
AvailabilityEvidence.EXPLICIT_EXTERNAL_EVIDENCE
```

La implementación de `bot_obrero/binance_spot.py` únicamente construye:

- `AvailabilityEvidence.unknown()`;
- `AvailabilityEvidence.consumer_handoff(...)`.

No se añade una vía para que Binance declare una disponibilidad externa anterior a `received_at`.

La semántica de `EXPLICIT_EXTERNAL_EVIDENCE` de FASE 1.13 permanece intacta y fuera del flujo Binance de esta fase.

---

## 7. Quality y completeness

La availability no reemplaza las demás dimensiones.

El adapter conserva:

```
quality = VALID
```

solo después de las validaciones existentes.

La disponibilidad conocida no cambia automáticamente:

```
completeness
```

Una prueba de FASE 1.14 confirma que un `MarketData` con availability conocida puede seguir teniendo:

```
completeness = UNKNOWN
```

según la semántica existente.

---

## 8. MarketData → MarketObservation

No se crea ninguna segunda vía de promoción.

Con:

```
quality = VALID
available_at != None
```

la ruta existente:

```
MarketData
    ↓
to_market_observation()
```

continúa funcionando.

Cuando:

```
available_at = None
```

la evidencia continúa bloqueada.

---

## 9. Look-ahead

FASE 1.14 no añade un nuevo guard.

Sigue siendo único:

```
EvidenceTimestamp
```

y su condición:

```
available_at <= decision_timestamp
```

Los tests cubren:

| Relación | Resultado |
|---|---|
| `available_at < decision_timestamp` | Permitido |
| `available_at == decision_timestamp` | Permitido |
| `available_at > decision_timestamp` | Rechazado |
| `available_at = None` | Evidencia bloqueada |

La comparación temporal no se duplica en Binance.

---

## 10. Handoff imposible

`AvailabilityEvidence.consumer_handoff()` conserva la defensa de FASE 1.13:

```
available_at < received_at
```

es rechazado.

FASE 1.14 utiliza esta defensa directamente.

También se mantiene la exigencia de timestamp timezone-aware, y el clock de handoff es inyectable para tests.

No se introduce `datetime.now()` adicional ni una dependencia global.

---

## 11. Flujo final

```
Binance REST
    ↓
T1 — response completa recibida
    ↓
ProviderRecord
    ↓
T2 — parsing
    ↓
T3 — normalization
    ↓
T4 — canonical validation
    ↓
T5 — consumer handoff explícito, si existe
    ↓
available_at = T5
    ↓
MarketData
    ↓
EvidenceTimestamp
    ↓
MarketObservation
```

Cuando no existe T5 demostrable:

```
available_at = None
```

---

## 12. Legacy isolation

La integración continúa usando únicamente:

`bot_obrero.market_data.Candle`

No se utiliza:

`bot_obrero.data.Candle`

Los tests verifican explícitamente que ambas clases permanecen separadas.

---

## 13. Compatibilidad contractual

Sin cambios en:

- `bot_obrero/market_data.py`;
- `bot_obrero/temporal.py`;
- `bot_obrero/analysis_contracts.py`.

No se añaden:

- `provider_available_at`;
- `system_available_at`;
- `revision_id`;
- `supersedes`;
- `version`.

**Canonical Market Data Contract v1.0 — SIN CAMBIOS.**

---

## 14. Fuera de alcance

No se implementa:

- Binance WebSocket;
- otro exchange;
- CCXT;
- trading;
- órdenes;
- cuentas;
- API keys;
- signatures;
- persistencia;
- Redis;
- queues;
- workers;
- background processes;
- strategy;
- indicators;
- risk;
- execution;
- replay;
- FASE 1.15.

---

## 15. Estado de cierre

FASE 1.14 integra la disponibilidad explícita con Binance Spot REST sin convertir `received_at` en disponibilidad de forma implícita.

El default es conservador:

La documentación y el código deben interpretarse conjuntamente: `consumer_handoff_clock` marca una frontera **pre-return** observable dentro del adapter y no el instante exacto de recepción por el caller.


```
available_at = None
```

La disponibilidad conocida exige una frontera explícita de consumidor:

```
AvailabilityEvidence.consumer_handoff()
```

La validación canónica termina antes de cruzar esa frontera y `EvidenceTimestamp` continúa siendo el único guard de look-ahead.
