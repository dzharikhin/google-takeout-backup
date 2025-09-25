#!/usr/bin/env sh
docker-compose run --rm --remove-orphans backup > /tmp/gtb.out 2>&1
if [ $? -ne 0 ]; then
  echo "Backup run has failed"
  cat /tmp/gtb.out
  SUBJECT="Google Takeout Backup run failed"
else
  echo "Backup run is successful"
  SUBJECT="Google Takeout Backup run is successful"
fi
printf "Subject: $SUBJECT\n\n%s" "$(cat /tmp/gtb.out)"
