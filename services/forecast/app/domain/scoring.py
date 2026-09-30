"""Puntuación de precisión de un pronóstico frente a la observación real (RF10 / RF11).

La fórmula es un *parámetro del sistema*: pesos, tolerancias y umbrales viven en la tabla
``scoring_parameters`` (versionada). Este módulo es puro: recibe diccionarios y devuelve un resultado.

    score = 100 · Σ(wᵢ · sᵢ) / Σ(wᵢ)       sobre las variables disponibles en ambos lados
    sᵢ = max(0, 1 − |previsto − observado| / toleranciaᵢ)        temperatura, humedad, viento, precipitación
    s_lluvia = 1 − (p − o)²      p = probabilidad prevista (0-1), o = 1 si llovió (≥ umbral mm) y 0 si no
    s_condición = max(0, 1 − |severidad prevista − severidad observada| / 3)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from honolulo_common.conditions import CONDITIONS

VARIABLES = ("temperature", "rain", "humidity", "condition", "precipitation", "wind")

DEFAULT_PARAMS = {
    "weights": {"temperature": 0.25, "rain": 0.25, "humidity": 0.15,
                "condition": 0.15, "precipitation": 0.10, "wind": 0.10},
    "tolerances": {"temperature": 5.0, "humidity": 30.0, "wind": 15.0, "precipitation": 5.0},
    "rain_prob_threshold_pct": 50,
    "rain_observed_threshold_mm": 0.1,
    "labels": [
        {"min": 90, "label": "Muy buena precisión"},
        {"min": 75, "label": "Buena precisión"},
        {"min": 60, "label": "Precisión aceptable"},
        {"min": 0, "label": "Precisión baja"},
    ],
}

# Campos de la observación/pronóstico que alimentan cada variable continua.
_CONTINUOUS = {"temperature": "temperature", "humidity": "humidity",
               "wind": "wind_speed", "precipitation": "precipitation"}
_DIGITS = {"temperature": 1, "humidity": 0, "wind": 1, "precipitation": 1}


class NotEvaluable(Exception):
    """No hay variables comparables entre el pronóstico y la observación."""


@dataclass
class Evaluation:
    accuracy_score: float
    accuracy_label: str
    variables: dict = field(default_factory=dict)


def label_for(score: float, labels: list[dict]) -> str:
    for item in sorted(labels, key=lambda l: l["min"], reverse=True):
        if score >= item["min"]:
            return item["label"]
    return labels[-1]["label"]


def validate_params(data: dict) -> dict:
    """Valida y normaliza los parámetros; lanza ``ValueError`` con un mensaje legible."""
    weights = data.get("weights")
    if not isinstance(weights, dict) or not weights:
        raise ValueError("weights: se requiere un objeto con los pesos por variable.")
    unknown = set(weights) - set(VARIABLES)
    if unknown:
        raise ValueError(f"weights: variables desconocidas {sorted(unknown)}; use {list(VARIABLES)}.")
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0 for v in weights.values()):
        raise ValueError("weights: todos los pesos deben ser números mayores o iguales a 0.")
    if sum(weights.values()) <= 0:
        raise ValueError("weights: la suma de los pesos debe ser mayor que 0.")

    tolerances = data.get("tolerances")
    needed = set(_CONTINUOUS) & {v for v, w in weights.items() if w > 0}
    if not isinstance(tolerances, dict) or any(
            not isinstance(tolerances.get(k), (int, float)) or tolerances[k] <= 0 for k in needed):
        raise ValueError(f"tolerances: se requieren valores mayores que 0 para {sorted(needed)}.")

    threshold = data.get("rain_prob_threshold_pct", 50)
    if not isinstance(threshold, int) or not 0 <= threshold <= 100:
        raise ValueError("rain_prob_threshold_pct: entero entre 0 y 100.")
    mm = data.get("rain_observed_threshold_mm", 0.1)
    if not isinstance(mm, (int, float)) or mm < 0:
        raise ValueError("rain_observed_threshold_mm: número mayor o igual a 0.")

    labels = data.get("labels")
    if (not isinstance(labels, list) or not labels
            or any(not isinstance(l, dict) or not isinstance(l.get("label"), str) or not l["label"].strip()
                   or not isinstance(l.get("min"), (int, float)) for l in labels)
            or min(l["min"] for l in labels) > 0):
        raise ValueError("labels: lista de {min, label}; debe incluir una etiqueta con min = 0.")
    return {"weights": {k: float(v) for k, v in weights.items()},
            "tolerances": {k: float(v) for k, v in (tolerances or {}).items()},
            "rain_prob_threshold_pct": threshold, "rain_observed_threshold_mm": float(mm),
            "labels": sorted(({"min": l["min"], "label": l["label"].strip()} for l in labels),
                             key=lambda l: l["min"], reverse=True)}


def evaluate(forecast: dict, observed: dict, params: dict) -> Evaluation:
    """Compara un pronóstico con la observación. ``forecast``/``observed`` usan las claves del API:
    temperature, humidity, rain_probability, precipitation, wind_speed, condition_code."""
    weights, tolerances = params["weights"], params["tolerances"]
    variables: dict[str, dict] = {}
    scores: dict[str, float] = {}

    for name, key in _CONTINUOUS.items():
        pred, obs = forecast.get(key), observed.get(key)
        if pred is None or obs is None:
            continue
        error = abs(pred - obs)
        score = max(0.0, 1 - error / tolerances[name])
        digits = _DIGITS[name]
        variables[name] = {"predicted": round(pred, digits) if digits else int(round(pred)),
                           "observed": round(obs, digits) if digits else int(round(obs)),
                           "error": round(error, digits) if digits else int(round(error)),
                           "score": round(score, 3)}
        scores[name] = score

    prob, precip = forecast.get("rain_probability"), observed.get("precipitation")
    if prob is not None and precip is not None:
        rained = precip >= params["rain_observed_threshold_mm"]
        predicted = prob >= params["rain_prob_threshold_pct"]
        score = 1 - (prob / 100 - (1.0 if rained else 0.0)) ** 2
        variables["rain"] = {"predicted": predicted, "observed": rained, "correct": predicted == rained,
                             "probability": prob, "score": round(score, 3)}
        scores["rain"] = score

    f_code, o_code = forecast.get("condition_code"), observed.get("condition_code")
    if f_code in CONDITIONS and o_code in CONDITIONS:
        gap = abs(CONDITIONS[f_code].severity - CONDITIONS[o_code].severity)
        score = max(0.0, 1 - gap / 3)
        variables["condition"] = {"predicted": CONDITIONS[f_code].text, "observed": CONDITIONS[o_code].text,
                                  "predicted_code": f_code, "observed_code": o_code, "score": round(score, 3)}
        scores["condition"] = score

    used = {k: weights.get(k, 0.0) for k in scores if weights.get(k, 0.0) > 0}
    if not used:
        raise NotEvaluable("No hay variables comparables con peso mayor que 0.")
    total = sum(used.values())
    accuracy = round(100 * sum(weights[k] * scores[k] for k in used) / total, 1)
    return Evaluation(accuracy, label_for(accuracy, params["labels"]), variables)
