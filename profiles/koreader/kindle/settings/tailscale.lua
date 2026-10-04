-- Let the plugin manage KOReader's live proxy and restore it on disconnect.
return { set = { force_userspace = true, auto_http_proxy = true } }
