package app.skydispatch

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import java.util.UUID

/**
 * The one live connection to the PC for the whole app process: it holds the event stream open, reconnects after a
 * Wi-Fi drop, and tells the screens when to re-read their data. Screens never open the stream themselves.
 */
object Hub {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var job: Job? = null
    private var started: Connection? = null
    private var viewing: String? = null
    private var speaker: Speaker? = null

    val viewer: String = UUID.randomUUID().toString().replace("-", "")

    var api: Api? = null
        private set

    /** Goes up whenever something a screen shows may have changed (everything except the fast `state` ticks). */
    private val _version = MutableStateFlow(0)
    val version: StateFlow<Int> = _version.asStateFlow()

    /** The latest `state` event: live flight numbers. */
    private val _live = MutableStateFlow<JsonObject?>(null)
    val live: StateFlow<JsonObject?> = _live.asStateFlow()

    private val _online = MutableStateFlow(false)
    val online: StateFlow<Boolean> = _online.asStateFlow()

    /** Set when the PC says this phone has been removed. */
    private val _revoked = MutableStateFlow(false)
    val revoked: StateFlow<Boolean> = _revoked.asStateFlow()

    private val _toasts = MutableSharedFlow<Toast>(extraBufferCapacity = 16)
    val toasts: SharedFlow<Toast> = _toasts.asSharedFlow()

    /** Raw stream events, for the notifier. */
    private val _events = MutableSharedFlow<StreamEvent>(extraBufferCapacity = 64)
    val events: SharedFlow<StreamEvent> = _events.asSharedFlow()

    data class Toast(val tone: String, val text: String)

    fun toast(tone: String, text: String) { _toasts.tryEmit(Toast(tone, text)) }

    fun start(context: Context, c: Connection) {
        if (started == c && job?.isActive == true) return
        stop()
        started = c
        _revoked.value = false
        val a = Api(c.baseUrl, c.token)
        api = a
        val sp = speaker ?: Speaker(context.applicationContext).also { speaker = it }
        job = scope.launch {
            var wait = 1000L
            while (true) {
                try {
                    a.stream(viewer).collect { ev ->
                        if (!_online.value) { _online.value = true; wait = 1000L; _version.value++; sendView(a) }
                        handle(ev, a, sp)
                    }
                } catch (e: ApiException) {
                    if (e.status == 401) { _revoked.value = true; _online.value = false; return@launch }
                }
                _online.value = false
                delay(wait)
                wait = (wait * 2).coerceAtMost(10_000L)
            }
        }
    }

    fun stop() {
        job?.cancel(); job = null; started = null; api = null; _online.value = false; _live.value = null
        speaker?.stopAll()
    }

    private fun handle(ev: StreamEvent, a: Api, sp: Speaker) {
        _events.tryEmit(ev)
        when (ev.name) {
            "state" -> _live.value = ev.data
            "toast" -> toast(ev.data.str("level", "info"), ev.data.str("message"))
            "speech" -> sp.onSpeech(a, ev.data)
            "speech_stop" -> sp.onStop(ev.data.strings("keep"))
            "open" -> {}
            else -> _version.value++
        }
    }

    /** Tell the PC which conversation is on screen; it only sends speech for that one (plus the copilot in flight). */
    fun view(thread: String?) {
        viewing = thread
        api?.let { a -> scope.launch { sendView(a) } }
    }

    private suspend fun sendView(a: Api) {
        try {
            a.post("view", jsonOf("viewer" to viewer, "thread" to viewing))
        } catch (e: ApiException) { /* the next reconnect sends it again */ }
    }

    /** Stop any speech now (talking over the dispatcher should cut it off). */
    fun silence() {
        speaker?.stopAll()
        api?.let { a -> scope.launch { try { a.post("voice/silence") } catch (e: ApiException) { } } }
    }
}
