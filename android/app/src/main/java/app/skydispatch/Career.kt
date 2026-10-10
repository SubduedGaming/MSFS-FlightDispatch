package app.skydispatch

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

@Serializable data class Starter(val id: String, val name: String, val installed: Boolean = true)
@Serializable data class Choice(val id: String, val label: String)
@Serializable data class CareerDefaults(
    val name: String = "", val callsign: String = "", val home: String = "", val balance: Double = 0.0,
    val difficulty: String = "normal", val currency: String = "", val experience: String = "new",
    val aircraft: String = "",
)
@Serializable data class CareerOptions(
    val starters: List<Starter> = emptyList(), val experience: List<Choice> = emptyList(),
    val difficulty: List<String> = emptyList(), val currencies: List<String> = emptyList(),
    val defaults: CareerDefaults = CareerDefaults(),
)

/** What the player picked on the new-career screen. */
data class CareerForm(
    val name: String, val home: String, val callsign: String, val aircraft: String,
    val experience: String, val difficulty: String, val currency: String, val balance: String,
) {
    /** A plain-language problem with the form, or null when it can be sent. The PC checks everything again. */
    fun problem(): String? = when {
        name.isBlank() -> "Enter your pilot name."
        home.trim().length !in 3..4 -> "Enter your home airport's ICAO code, for example EGLL or KSEA."
        aircraft.isBlank() -> "Choose a starter aircraft."
        balance.isNotBlank() && balance.toDoubleOrNull()?.let { it in 0.0..5_000_000.0 } != true ->
            "The starting balance must be a number from 0 to 5,000,000."
        else -> null
    }

    fun toJson(): JsonObject = buildJsonObject {
        put("name", name.trim())
        put("home", home.trim().uppercase())
        put("aircraft", aircraft)
        if (callsign.isNotBlank()) put("callsign", callsign.trim())
        put("experience", experience)
        put("difficulty", difficulty)
        if (currency.isNotBlank()) put("currency", currency)
        balance.toDoubleOrNull()?.let { put("balance", it) }
    }
}
