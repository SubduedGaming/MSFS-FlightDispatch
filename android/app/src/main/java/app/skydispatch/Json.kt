package app.skydispatch

import kotlinx.serialization.json.*

/*
 * The server's answers are shaped for display and change as the game grows, so the screens read them leniently:
 * a missing or null field is an empty string, 0 or false instead of a crash.
 */

fun JsonObject.str(k: String, d: String = ""): String =
    (this[k] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.content ?: d

fun JsonObject.int(k: String, d: Int = 0): Int =
    (this[k] as? JsonPrimitive)?.let { it.intOrNull ?: it.doubleOrNull?.toInt() } ?: d

fun JsonObject.dbl(k: String, d: Double = 0.0): Double = (this[k] as? JsonPrimitive)?.doubleOrNull ?: d

fun JsonObject.bool(k: String, d: Boolean = false): Boolean = (this[k] as? JsonPrimitive)?.booleanOrNull ?: d

fun JsonObject.obj(k: String): JsonObject? = this[k] as? JsonObject

fun JsonObject.list(k: String): List<JsonObject> = (this[k] as? JsonArray)?.mapNotNull { it as? JsonObject } ?: emptyList()

fun JsonObject.strings(k: String): List<String> =
    (this[k] as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.takeIf { p -> p !is JsonNull }?.content } ?: emptyList()

/** Build a request body from pairs; null values are left out. */
fun jsonOf(vararg pairs: Pair<String, Any?>): JsonObject = buildJsonObject {
    for ((k, v) in pairs) when (v) {
        null -> {}
        is Boolean -> put(k, v)
        is Number -> put(k, v)
        is String -> put(k, v)
        is JsonElement -> put(k, v)
        else -> put(k, v.toString())
    }
}
