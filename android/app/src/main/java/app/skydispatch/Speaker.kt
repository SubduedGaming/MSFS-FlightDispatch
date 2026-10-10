package app.skydispatch

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import java.io.File
import kotlin.coroutines.resume

/**
 * Plays what the PC says through the phone's speaker: a WAV from the PC when it has a voice for the character, or
 * Android's own text-to-speech when it does not. One thing at a time, in order; `speech_stop` cuts off anything that
 * is not for a conversation still on screen.
 */
class Speaker(private val context: Context) {
    private data class Utterance(val id: String, val thread: String, val text: String, val audio: Boolean, val speed: Float)

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val queue = ArrayDeque<Utterance>()
    private val wake = Channel<Unit>(Channel.CONFLATED)
    private var current: Utterance? = null
    private var playing: Job? = null
    private var player: MediaPlayer? = null
    private var tts: TextToSpeech? = null
    private var ttsReady = false

    init {
        scope.launch {
            for (ignored in wake) {
                while (true) {
                    val next = queue.removeFirstOrNull() ?: break
                    current = next
                    playing = launch { play(next) }
                    playing?.join()
                    current = null
                }
            }
        }
    }

    fun onSpeech(api: Api, data: JsonObject) {
        val id = data.str("id")
        if (id.isEmpty() || data.str("text").isBlank()) return
        val u = Utterance(id, data.str("thread"), data.str("text"), data.bool("audio"), data.dbl("speed", 1.0).toFloat())
        queue.addLast(u)
        this.api = api
        wake.trySend(Unit)
    }

    private var api: Api? = null

    /** Keep only speech for the conversations in [keep]; an empty list stops everything. */
    fun onStop(keep: List<String>) {
        queue.removeAll { it.thread !in keep }
        if (current != null && current?.thread !in keep) cancelCurrent()
    }

    fun stopAll() {
        queue.clear()
        cancelCurrent()
    }

    private fun cancelCurrent() {
        playing?.cancel()
        tts?.stop()
    }

    private suspend fun play(u: Utterance) {
        try {
            if (u.audio) {
                val wav = api?.audio(u.id)
                if (wav != null && wav.isNotEmpty()) { playWav(wav); return }
            }
            speakText(u)
        } catch (e: ApiException) {
            speakText(u)                       // the PC could not make the audio; the phone can still read it out
        }
    }

    private suspend fun playWav(wav: ByteArray) {
        val file = withContext(Dispatchers.IO) {
            File.createTempFile("speech", ".wav", context.cacheDir).also { it.writeBytes(wav) }
        }
        try {
            suspendCancellableCoroutine { cont ->
                val mp = MediaPlayer()
                player = mp
                mp.setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                mp.setDataSource(file.path)
                mp.setOnCompletionListener { if (cont.isActive) cont.resume(Unit) }
                mp.setOnErrorListener { _, _, _ -> if (cont.isActive) cont.resume(Unit); true }
                mp.setOnPreparedListener { it.start() }
                cont.invokeOnCancellation { runCatching { mp.stop() }; mp.release(); if (player === mp) player = null }
                mp.prepareAsync()
            }
        } finally {
            player?.let { runCatching { it.release() } }
            player = null
            file.delete()
        }
    }

    private suspend fun speakText(u: Utterance) {
        val engine = ensureTts() ?: return
        suspendCancellableCoroutine { cont ->
            engine.setSpeechRate(u.speed.coerceIn(0.5f, 2f))
            engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {}
                override fun onDone(utteranceId: String?) { if (cont.isActive) cont.resume(Unit) }
                @Deprecated("Deprecated in Java") override fun onError(utteranceId: String?) { if (cont.isActive) cont.resume(Unit) }
                override fun onStop(utteranceId: String?, interrupted: Boolean) { if (cont.isActive) cont.resume(Unit) }
            })
            cont.invokeOnCancellation { engine.stop() }
            engine.speak(u.text, TextToSpeech.QUEUE_FLUSH, null, u.id)
        }
    }

    private suspend fun ensureTts(): TextToSpeech? {
        tts?.let { if (ttsReady) return it }
        return suspendCancellableCoroutine { cont ->
            val engine = TextToSpeech(context) { status ->
                ttsReady = status == TextToSpeech.SUCCESS
                if (cont.isActive) cont.resume(if (ttsReady) tts else null)
            }
            tts = engine
        }
    }
}
