FROM python:3.13-alpine
ARG BUILD_VERSION=0.3.5
ARG BUILD_ARCH=amd64
LABEL io.hass.version=$BUILD_VERSION io.hass.type="app" io.hass.arch=$BUILD_ARCH
WORKDIR /app
COPY app.py /app/app.py
COPY deployment.py /app/deployment.py
EXPOSE 8099
CMD ["python", "-u", "/app/app.py"]
