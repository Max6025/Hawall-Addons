ARG BUILD_FROM
FROM $BUILD_FROM

# Abhängigkeiten zuerst, damit ein Änderung am Quelltext nicht jedes Mal pip neu laufen
# lässt. Die Fassungen sind in requirements.txt festgenagelt -- siehe Begründung dort.
COPY requirements.txt /tmp/requirements.txt
RUN apk add --no-cache tzdata \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY wartungsmelder.py /app/wartungsmelder.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Ungepuffert, sonst erscheinen die Protokollzeilen im Add-on-Protokoll erst verspätet oder
# beim Absturz gar nicht -- und dann sucht man den Fehler an der falschen Stelle.
ENV PYTHONUNBUFFERED=1

CMD [ "/run.sh" ]
