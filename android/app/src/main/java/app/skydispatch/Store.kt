package app.skydispatch

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.first

private val Context.ds by preferencesDataStore("skydispatch")

data class Connection(val baseUrl: String, val token: String)

class Store(private val context: Context) {
    private val url = stringPreferencesKey("base_url")
    private val tok = stringPreferencesKey("token")

    suspend fun load(): Connection? {
        val p = context.ds.data.first()
        val u = p[url]
        val t = p[tok]
        return if (u != null && t != null) Connection(u, t) else null
    }

    suspend fun save(c: Connection) { context.ds.edit { it[url] = c.baseUrl; it[tok] = c.token } }
    suspend fun flag(name: String): Boolean = context.ds.data.first()[booleanPreferencesKey("flag_$name")] == true
    suspend fun setFlag(name: String) { context.ds.edit { it[booleanPreferencesKey("flag_$name")] = true } }
    suspend fun clear() { context.ds.edit { it.clear() } }
}
