{{- define "jiro.fullname" -}}
{{- printf "%s-%s" .Release.Name "jiro" | trunc 63 | trimSuffix "-" -}}
{{- end -}}
