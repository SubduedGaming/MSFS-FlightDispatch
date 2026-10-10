package app.skydispatch

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

@Serializable data class Ping(val app: String, val version: String, val api: Int, val authed: Boolean)
@Serializable data class Paired(val token: String, val device_id: String)

/** A problem to show the player: [message] is already plain language. */
class ApiException(message: String, val status: Int = 0) : IOException(message)

/** One Server-Sent Event: `state`, `thread`, `speech`, ... and its JSON payload. */
data class StreamEvent(val name: String, val data: JsonObject)

class Api(private val baseUrl: String, private val token: String? = null) {
    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }
    private val http = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS).readTimeout(20, TimeUnit.SECONDS).build()
    // The server sends a keep-alive every 15 s, so silence for 45 s means the connection is dead.
    private val streamHttp = http.newBuilder().readTimeout(45, TimeUnit.SECONDS).build()

    private fun request(path: String): Request.Builder {
        val url = "$baseUrl/api/v1/$path"
        // The connection is plain HTTP, so only ever talk to addresses on the home network.
        val host = url.toHttpUrlOrNull()?.host
        if (host == null || !PairingLink.isPrivateHost(host)) {
            throw ApiException("Use the PC's home-network address (it usually starts with 192.168 or 10).")
        }
        val b = Request.Builder().url(url)
        if (token != null) b.header("Authorization", "Bearer $token")
        return b
    }

    private fun failure(code: Int, text: String): ApiException {
        val msg = try {
            json.parseToJsonElement(text).jsonObject["error"]?.jsonPrimitive?.content
        } catch (e: Exception) { null }
        return ApiException(msg ?: "The PC answered with error $code.", code)
    }

    private suspend fun <T> io(what: String = "", block: () -> T): T = withContext(Dispatchers.IO) {
        try {
            block()
        } catch (e: ApiException) {
            throw e
        } catch (e: IOException) {
            try { android.util.Log.w("SkyDispatch", "request failed $what: $e") } catch (ignored: Throwable) { }   // not available in unit tests
            throw ApiException("Could not reach the PC. Check that it is on and on the same Wi-Fi.")
        }
    }

    private suspend fun call(path: String, body: String? = null): String {
        suspend fun once(): String = io(path) {
            val b = request(path)
            if (body != null) b.header("X-SkyDispatch", "1").post(body.toRequestBody("application/json".toMediaType()))
            http.newCall(b.build()).execute().use { r ->
                val text = r.body?.string().orEmpty()
                if (!r.isSuccessful) throw failure(r.code, text)
                text
            }
        }
        // The PC closes each connection after answering, so a read that lands on a dropped one is simply repeated.
        // Writes are never repeated: they could happen twice.
        return if (body == null) try { once() } catch (e: ApiException) { if (e.status == 0) once() else throw e } else once()
    }

    suspend fun ping(): Ping = json.decodeFromString(call("ping"))

    suspend fun pair(code: String, deviceName: String): Paired {
        val body = buildJsonObject { put("code", code); put("device_name", deviceName.take(40)) }.toString()
        return json.decodeFromString(call("pair", body))
    }

    suspend fun state(): JsonObject = get("state")

    suspend fun get(path: String): JsonObject = json.parseToJsonElement(call(path)).jsonObject

    suspend fun post(path: String, body: JsonObject = JsonObject(emptyMap())): JsonObject {
        val text = call(path, body.toString())
        return if (text.isBlank()) JsonObject(emptyMap()) else json.parseToJsonElement(text).jsonObject
    }

    // Kept for the older call sites.
    suspend fun getObject(path: String): JsonObject = get(path)
    suspend fun postObject(path: String, body: JsonObject): JsonObject = post(path, body)

    suspend fun careerOptions(): CareerOptions = json.decodeFromString(call("career/options"))

    suspend fun createCareer(body: JsonObject) { call("career", body.toString()) }

    /** A recording (WAV) for the PC to transcribe. [send] names a conversation to say it in. */
    suspend fun transcribe(wav: ByteArray, send: String?): JsonObject = io {
        val q = if (send != null) "?send=$send" else ""
        val req = request("voice/transcribe$q").header("X-SkyDispatch", "1")
            .post(wav.toRequestBody("audio/wav".toMediaType())).build()
        // Recognition can take a while on a slow PC.
        http.newBuilder().readTimeout(90, TimeUnit.SECONDS).build().newCall(req).execute().use { r ->
            val text = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw failure(r.code, text)
            json.parseToJsonElement(text).jsonObject
        }
    }

    /** The WAV the PC rendered for a `speech` event. */
    suspend fun audio(id: String): ByteArray = io {
        http.newBuilder().readTimeout(60, TimeUnit.SECONDS).build().newCall(request("voice/audio/$id").build()).execute().use { r ->
            if (!r.isSuccessful) throw failure(r.code, r.body?.string().orEmpty())
            r.body?.bytes() ?: ByteArray(0)
        }
    }

    /** The live event stream. Ends (with an exception) when the connection drops; the caller reconnects. */
    fun stream(viewer: String): Flow<StreamEvent> = flow {
        val call = streamHttp.newCall(request("stream?v=$viewer").build())
        currentCoroutineContext()[Job]?.invokeOnCompletion { call.cancel() }
        val resp = try { call.execute() } catch (e: IOException) { throw ApiException("Lost the connection to the PC.") }
        resp.use { r ->
            if (!r.isSuccessful) throw failure(r.code, r.body?.string().orEmpty())
            val src = r.body?.source() ?: return@use
            emit(StreamEvent("open", JsonObject(emptyMap())))      // connected, even if nothing has happened yet
            var name = ""
            var data = StringBuilder()
            try {
                while (true) {
                    val line = src.readUtf8Line() ?: break
                    when {
                        line.isEmpty() -> {
                            if (name.isNotEmpty() && data.isNotEmpty()) {
                                val obj = try { json.parseToJsonElement(data.toString()).jsonObject } catch (e: Exception) { null }
                                if (obj != null) emit(StreamEvent(name, obj))
                            }
                            name = ""; data = StringBuilder()
                        }
                        line.startsWith("event:") -> name = line.substring(6).trim()
                        line.startsWith("data:") -> { if (data.isNotEmpty()) data.append('\n'); data.append(line.substring(5).trimStart()) }
                    }
                }
            } catch (e: IOException) {
                throw ApiException("Lost the connection to the PC.")
            }
        }
    }.flowOn(Dispatchers.IO)
}

/** Check that [host]:[port] is a SkyDispatch server on the home network and trade [code] for a device token. */
suspend fun pairWith(host: String, port: Int, code: String, deviceName: String): Connection {
    if (port !in 1..65535) throw ApiException("That port is not valid.")
    if (!PairingLink.isPrivateHost(host)) {
        throw ApiException("Use the PC's home-network address (it usually starts with 192.168 or 10).")
    }
    val base = "http://${host.trim()}:$port"
    if (Api(base).ping().app != "SkyDispatch") throw ApiException("That is not a SkyDispatch server.")
    return Connection(base, Api(base).pair(code.trim(), deviceName).token)
}
