#!/usr/bin/env sh
docker-compose run --rm --remove-orphans backup > /tmp/gtb.out 2>&1
if [ $? -ne 0 ]; then
  echo "Backup run has failed"
  SUBJECT="Google Takeout Backup run failed"
else
  echo "Backup run is successful"
  SUBJECT="Google Takeout Backup run is successful"
fi
echo -e "Subject: $SUBJECT\n\n$(cat /tmp/gtb.out)" | ssmtp root
