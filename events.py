"""Small newline-delimited worker telemetry contract shared by both interfaces."""
import json
import re

PREFIX='SH_EVENT '


def event_line(message='',progress=None):
    event={'message':str(message)}
    if progress is not None: event['progress']=max(0,min(100,float(progress)))
    return PREFIX+json.dumps(event,ensure_ascii=True)


def parse_line(line):
    if line.startswith(PREFIX):
        try:
            event=json.loads(line[len(PREFIX):])
            message=str(event.get('message',''))
            progress=event.get('progress')
            if progress is not None: progress=max(0,min(100,float(progress)))
        except (ValueError,TypeError): message,progress=line,None
    else: message,progress=line,None
    return re.sub(r'https?://\S+','[remote asset]',message),progress
