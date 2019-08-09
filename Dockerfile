FROM python:3.7

RUN groupadd --gid 10001 app && \
    useradd -g app --uid 10001 --shell /usr/sbin/nologin --create-home --home-dir /app app

RUN apt-get update \
 && apt-get install -y zipalign default-jdk-headless \
 && ln -s /app/docker.d/bin/healthcheck /bin/healthcheck

USER app
WORKDIR /app

COPY . /app

RUN python -m venv /app \
 && ./bin/pip install -r requirements/base.txt \
 && ./bin/pip install -e . \
 && ./bin/pip install https://github.com/rail/configloader/archive/d0336ed42f364ae5da749851d855ada1d6ff9951.tar.gz \
 && wget -O ./bin/dmg https://github.com/mozilla-releng/build-puppet/raw/master/modules/signing_scriptworker/files/dmg/dmg \
 && wget -O ./bin/hfsplus https://github.com/mozilla-releng/build-puppet/raw/master/modules/signing_scriptworker/files/dmg/hfsplus \
 && chmod 755 ./bin/dmg ./bin/hfsplus

CMD ["/app/docker.d/init.sh"]
