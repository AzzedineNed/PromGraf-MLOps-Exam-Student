#!/usr/bin/env bash
# Generates traffic on /predict so the API Performance dashboard has data.
# A fraction of the requests are deliberately malformed so that the error-rate
# panel and the HighApiErrorRate alert are exercised too.
set -u

API_URL="${API_URL:-http://localhost:8080}"
COUNT="${COUNT:-120}"
SLEEP="${SLEEP:-0.3}"

echo "Sending ${COUNT} requests to ${API_URL}/predict ..."

for i in $(seq 1 "${COUNT}"); do
  if [ $((i % 12)) -eq 0 ]; then
    # Missing required features on purpose -> 422, recorded as an API error.
    curl -s -o /dev/null -X POST "${API_URL}/predict" \
      -H 'Content-Type: application/json' \
      -d '{"temp": 0.5}'
  else
    HOUR=$((RANDOM % 24))
    WDAY=$((RANDOM % 7))
    TEMP=$(awk -v s="${RANDOM}" 'BEGIN{srand(s); printf "%.2f", 0.05 + rand()*0.55}')
    HUM=$(awk -v s="${RANDOM}" 'BEGIN{srand(s); printf "%.2f", 0.25 + rand()*0.70}')
    WIND=$(awk -v s="${RANDOM}" 'BEGIN{srand(s); printf "%.4f", rand()*0.45}')
    WEATHER=$((RANDOM % 3 + 1))
    WORKING=$(( WDAY < 5 ? 1 : 0 ))

    curl -s -o /dev/null -X POST "${API_URL}/predict" \
      -H 'Content-Type: application/json' \
      -d "{\"temp\": ${TEMP}, \"atemp\": ${TEMP}, \"hum\": ${HUM}, \"windspeed\": ${WIND}, \"mnth\": 1, \"hr\": ${HOUR}, \"weekday\": ${WDAY}, \"season\": 1, \"holiday\": 0, \"workingday\": ${WORKING}, \"weathersit\": ${WEATHER}, \"dteday\": \"2011-01-15\"}"
  fi

  if [ $((i % 20)) -eq 0 ]; then
    echo "  ${i}/${COUNT} sent"
  fi
  sleep "${SLEEP}"
done

echo "Done. ${COUNT} requests sent."
