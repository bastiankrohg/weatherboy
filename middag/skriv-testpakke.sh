#!/bin/sh
# Skriv ut testpakken NÅ via weatherboy-sida, fra en maskin på hjemme-WiFi-en (tunnelen slipper bare inn
# flatens egen adresse). Krever ingenting annet enn curl.
#
#   sh middag/skriv-testpakke.sh                       # via https://print.bastiankrohg.com
#   sh middag/skriv-testpakke.sh http://<mac>:8615     # rett på maskinen, hvis du er på samme nett
URL=${1:-https://print.bastiankrohg.com}
DIR=$(cd "$(dirname "$0")" && pwd)/billettar
for f in "$DIR"/0*.png "$DIR"/1*.png; do
  name=$(basename "$f" .png)
  printf '%-22s ' "$name"
  curl -fsS --data-binary @"$f" -H "Content-Type: image/png" \
    "$URL/api/image?print=1&label=middag-$name" -o /dev/null -D - \
    | grep -i -E '^x-(printed|queued|why)' | tr -d '\r' | tr '\n' ' '
  echo
  sleep 3
done
