package app.skydispatch

import java.net.URI
import java.net.URLDecoder

/** What the QR code on the PC holds: `skydispatch://pair?host=..&port=..&code=..`. */
data class PairingLink(val host: String, val port: Int, val code: String) {
    companion object {
        fun parse(text: String): PairingLink? {
            val uri = try { URI(text.trim()) } catch (e: Exception) { return null }
            if (uri.scheme != "skydispatch" || uri.host != "pair") return null
            val q = (uri.rawQuery ?: return null).split("&").mapNotNull {
                val i = it.indexOf('=')
                if (i < 0) null else URLDecoder.decode(it.substring(0, i), "UTF-8") to
                    URLDecoder.decode(it.substring(i + 1), "UTF-8")
            }.toMap()
            val host = q["host"]?.takeIf { it.isNotBlank() } ?: return null
            val port = q["port"]?.toIntOrNull()?.takeIf { it in 1..65535 } ?: return null
            val code = q["code"]?.takeIf { it.isNotBlank() } ?: return null
            return PairingLink(host, port, code)
        }

        /** Only talk to addresses on the home network, since the connection is plain HTTP. */
        fun isPrivateHost(host: String): Boolean {
            val h = host.trim().lowercase()
            if (h == "localhost" || h.endsWith(".local") || h.endsWith(".lan")) return true
            val p = h.split(".").mapNotNull { it.toIntOrNull() }
            if (p.size != 4 || p.any { it !in 0..255 }) return false
            return p[0] == 10 || p[0] == 127 || (p[0] == 192 && p[1] == 168) ||
                (p[0] == 172 && p[1] in 16..31) || (p[0] == 169 && p[1] == 254)
        }
    }
}
