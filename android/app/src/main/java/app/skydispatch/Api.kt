package app.skydispatch

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
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

class Api(private val baseUrl: String, private val token: String? = null) {
    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }
    private val http = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS).readTimeout(20, TimeUnit.SECONDS).build()

    private suspend fun call(path: String, body: String? = null): String = withContext(Dispatchers.IO) {
        val b = Request.Builder().url("$baseUrl/api/v1/$path")
        if (token != null) b.header("Authorization", "Bearer $token")
        if (body != null) b.header("X-SkyDispatch", "1").post(body.toRequestBody("application/json".toMediaType()))
        try {
            http.newCall(b.build()).execute().use { r ->
                val text = r.body?.string().orEmpty()
                if (!r.isSuccessful) {
                    val msg = try {
                        json.parseToJsonElement(text).jsonObject["error"]?.jsonPrimitive?.content
                    } catch (e: Exception) { null }
                    throw ApiException(msg ?: "The PC answered with error ${r.code}.", r.code)
                }
                text
            }
        } catch (e: ApiException) {
            throw e
        } catch (e: IOException) {
            throw ApiException("Could not reach the PC. Check the address and that both are on the same Wi-Fi.")
        }
    }

    suspend fun ping(): Ping = json.decodeFromString(call("ping"))

    suspend fun pair(code: String, deviceName: String): Paired {
        val body = buildJsonObject { put("code", code); put("device_name", deviceName.take(40)) }.toString()
        return json.decodeFromString(call("pair", body))
    }

    suspend fun state(): JsonObject = getObject("state")

    suspend fun getObject(path: String): JsonObject = json.parseToJsonElement(call(path)).jsonObject

    suspend fun postObject(path: String, body: JsonObject): JsonObject =
        json.parseToJsonElement(call(path, body.toString())).jsonObject

    suspend fun careerOptions(): CareerOptions = json.decodeFromString(call("career/options"))

    suspend fun createCareer(body: JsonObject) { call("career", body.toString()) }
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
