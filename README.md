# Google photo takeout backup
Docker compose based app able to backup google photo via takeout by link

Google makes it very hard to automate takeout management - but it is possible

# Prerequisites
1. mainstream arch like `x86_64` - to be able to run [playwright](https://github.com/microsoft/playwright) and [gpth](https://github.com/TheLastGimbus/GooglePhotosTakeoutHelper)
2. `docker`,`docker-compose`
3. `crontab` or another way to schedule automation and notify if something goes wrong
4. `ssmtp` or another channel to notify you about backup launch status
5. Any storage to mount to store backup in

# Arch
The app consists of two main parts:
1. Backup server - runs backup script, stores results to FS, provides auth info
2. Browser server - runs browser, ensures security of auth info

# How to use

## Browser server
Browser server services should interact via secure network exposing only one public proxy port

You can run browser server on a dedicated node if it is connected to backup server
1. go to [browser-server](./browser-server)
2. `docker network create --opt encrypted --attachable secure_net`
3. `docker-compose up browser-manual`
4. authorize, then just close browser, copy auth data(from console output)
5. `docker-compose down browser-manual`
6. `docker-compose up`
7. store link `Encode pass with public key...` from logs

## Backup server
1. go to [backup-server](./backup-server)
   - `downloads` - working dir for backup intermediate processing: downloading, unpacking, sorting etc - can be local FS
   - `photos` - that's where final backups are stored to
   - `keys_RU.csv` - locale-dependent button names to interact with browser UI controls. If you need another locale, see how to use `GOOGLE_LANG` env param
      > there's no way to use locale-agnostic selectors there - css-classes are obfuscated and are changing ;(
2. create `.env` and fill it with required values
   1. open link from `browser-server`(7)
   2. fill `ENCODED_STATE`, `ENCODED_PASS` with values obtained from `browser-server`(4) and your password
   > `ENCODED_STATE`, `ENCODED_PASS` need to be encoded with new key each browser-server encryption keys are updated  
   > By default encryption state is generated on `browser-server` startup, 
   > but if you run `browser-server` on a dedicated secure-enough node, you can provide stable keys from the env 
3. schedule command `docker-compose run backup` to execute in [backup-server](./backup-server) working directory frequently enough for the backup purposes
   1. there is [script](./backup-server/execute_backup.sh) as a **skeleton**(please set sending command) for scheduling execution
   > it is nice to have tool like `ssmtp` configured to be aware of success and failure runs.  
   > You can use command exit code to distinguish errors  
   > From time to time you need to reset `ENCODED_STATE` manually to keep the account authorized  
