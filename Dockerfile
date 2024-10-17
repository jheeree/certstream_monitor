# Usa la imagen base de Python
FROM python:3.9-slim

# Establece el directorio de trabajo
WORKDIR /usr/src/app

# Copia el archivo requirements.txt al contenedor
COPY requirements.txt ./

# Instala las dependencias
RUN pip install --no-cache-dir -r requirements.txt

# Copia el script al contenedor
COPY CertStream_Monitor.py ./
COPY config.ini ./
