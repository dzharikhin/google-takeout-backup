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
   > if GUI display is not available on `browser-server` node - you can launch the script on any other device, auth data is compatible between different browser instances
   > 
   > it may be more reasonable to call [script](browser-server/manual_auth.py) directly, without docker
   > the script sets up browser to use `wayland` - if you need `X11` - tweak the script(browser launch args)
4. authorize, then just close browser, copy auth data(from console output)
5. `docker-compose down browser-manual`
6. `docker-compose up`
7. store link `Encode pass with public key...` from logs

## Backup server
1. go to [backup-server](./backup-server)
   - `keys_RU.csv` - locale-dependent button names to interact with browser UI controls. If you need another locale, see how to use `GOOGLE_LANG` env param
   > there's no way to use locale-agnostic selectors there - css-classes are obfuscated and are changing ;(
2. create `downloads` dir - it's for backup intermediate processing: downloading, unpacking, sorting, etc - can be local FS
3. create `photos` dir - it's where final backups are stored to. If you have dedicated storage - here's convenient mount point
4. create `.auth_encoded` file - open link from `browser-server`(7), encode data from `browser-server`(4) and paste encoded value into the file
5. create `.env` file - open link from `browser-server`(7), encode your **password** and create var `ENCODED_PASS` with encoded value in the file
   > `.auth_encoded` and `ENCODED_PASS` need to be encoded with a new key each time `browser-server` encryption keys are updated  
   > 
   > By default `browser-server` encryption state is generated on start, 
   > but if you run `browser-server` on a dedicated secure-enough node, you can provide fixed keys from the env
6. schedule command `docker-compose run backup` to execute in [backup-server](./backup-server) working directory frequently enough for the backup purposes
   > there is [skeleton](./backup-server/execute_backup.sh) for scheduling execution  
   > but it requires local customization to be used