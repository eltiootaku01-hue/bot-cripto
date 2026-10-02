# FASE 1.13 — Frontera canónica de disponibilidad

## 1. Relación con FASE 1.12

FASE 1.12 definió la política normativa: available_at es el primer instante demostrable en que la evidencia concreta estuvo disponible para el consumidor relevante dentro del sistema.

FASE 1.13 implementa esa política como una etapa explícita antes de la canonicalización. No modifica el contrato canónico y no cambia el adapter Binance de FASE 1.11.

## 2. Flujo operativo

ProviderRecord
      ↓
parse_provider_payload()
      ↓
normalize_provider_record()
      ↓
AvailabilityEvidence
      ↓
apply_availability_evidence()
      ↓
canonicalize_market_data()
      ↓
MarketData
      ↓
EvidenceTimestamp
      ↓
MarketObservation

La disponibilidad no se infiere dentro de la normalización.

## 3. AvailabilityEvidence

Se añadió bot_obrero/availability.py con cuatro tipos explícitos:

- UNKNOWN
- RECEIVED_AND_AVAILABLE
- CONSUMER_HANDOFF
- EXPLICIT_EXTERNAL_EVIDENCE

### UNKNOWN

Representa ausencia de evidencia suficiente.

Resultado:

available_at = None

No existe fallback a received_at ni observed_at.

### RECEIVED_AND_AVAILABLE

Declara explícitamente que la frontera de recepción es también la frontera desde la cual el consumidor puede acceder al dato.

Resultado:

available_at = received_at

La igualdad solo existe porque el tipo de evidencia lo declara.

### CONSUMER_HANDOFF

Representa una frontera de consumidor posterior a la recepción:

received_at = T1
available_at = T2
T2 >= T1

Una disponibilidad anterior a received_at se rechaza en este modo.

### EXPLICIT_EXTERNAL_EVIDENCE

Representa evidencia externa explícita que, por su propia semántica, demuestra la disponibilidad relevante para el consumidor.

Requiere:

- timestamp timezone-aware;
- evidence_reference explícita;
- consumer_scope explícito.

No se aplica una regla universal received_at <= available_at a este tipo. La razón es preservar la política de 1.12: una fuente externa solo puede respaldar availability si su semántica demuestra realmente la disponibilidad relevante; el adapter no puede inventarla ni reescribirla.

## 4. Regla de no inferencia

Está prohibido resolver disponibilidad así:

UNKNOWN → received_at
UNKNOWN → observed_at
received_at existente → available_at automático
observed_at → available_at automático

La única fuente de resolution es AvailabilityEvidence.

## 5. Integración con FASE 1.10

La función apply_availability_evidence() recibe NormalizedMarketDataInput y devuelve una nueva NormalizationResult con available_at resuelto.

La función provider_payload_to_market_data_with_availability() muestra el flujo completo:

ProviderRecord → Parse → Normalize → AvailabilityEvidence → Canonical Validation → MarketData

La canonicalización continúa delegando OHLCV, Candle, MarketData, InstrumentIdentity y SourceIdentity a las abstracciones existentes.

## 6. Look-ahead

FASE 1.13 no crea un segundo guard temporal.

EvidenceTimestamp continúa siendo el único mecanismo que valida:

available_at <= decision_timestamp

FASE 1.13 solamente determina qué available_at llega al MarketData.

Por tanto:

- decision_timestamp no se añade a AvailabilityEvidence;
- EvidenceTimestamp permanece intacto;
- no existe una segunda lógica de look-ahead.

## 7. UNKNOWN y promoción

Si AvailabilityEvidence.UNKNOWN produce available_at=None, MarketData permanece válido como objeto de mercado pero:

- MarketData.evidence_at() queda bloqueado;
- to_market_observation() queda bloqueado;
- una decisión no puede usar esa evidencia temporalmente.

## 8. Binance Spot 1.11

No se modificó bot_obrero/binance_spot.py.

El adapter Binance continúa dejando:

available_at = None

hasta que una futura integración pueda demostrar una frontera real de consumidor. La nueva infraestructura permite expresar RECEIVED_AND_AVAILABLE o CONSUMER_HANDOFF sin cambiar el adapter 1.11.

## 9. Legacy isolation

AvailabilityEvidence no depende de bot_obrero.data.Candle.

La integración continúa utilizando únicamente el Candle canónico de bot_obrero.market_data.

## 10. Tests

Se añadió tests/test_phase1_13_availability.py cubriendo:

- UNKNOWN;
- received=available explícito;
- consumer handoff;
- handoff anterior rechazado;
- ausencia de fallback;
- observed_at no utilizado automáticamente;
- evidencia externa explícita;
- look-ahead mediante EvidenceTimestamp;
- bloqueo de evidence_at() con UNKNOWN;
- bloqueo de promoción con UNKNOWN;
- bloqueo de promoción por quality != VALID;
- canonical validation después de availability;
- aislamiento del Candle legacy;
- ausencia de decision_timestamp dentro de AvailabilityEvidence.

## 11. Fuera de alcance

Esta fase no implementa:

- Binance WebSocket;
- otros exchanges;
- persistencia;
- Redis;
- colas;
- workers;
- replay engine;
- versionado histórico;
- trading;
- estrategia;
- riesgo;
- ejecución;
- cambios en market_data.py;
- cambios en Binance 1.11.

## 12. Contrato canónico

Canonical Market Data Contract v1.0 permanece sin cambios.

No se añaden provider_available_at, system_available_at, revision_id, supersedes ni version al contrato.

## 13. Estado

FASE 1.13 implementa la frontera operativa sin reemplazar la política de 1.12 ni duplicar EvidenceTimestamp.