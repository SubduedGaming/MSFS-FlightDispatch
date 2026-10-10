package app.skydispatch

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Hold-to-talk: records 16 kHz mono 16-bit sound from the microphone and hands back a WAV file. */
class Recorder {
    private var record: AudioRecord? = null
    private var thread: Thread? = null
    private val pcm = ByteArrayOutputStream()
    @Volatile private var running = false

    /** False when the microphone could not be opened (no permission, or another app is using it). */
    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        val min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (min <= 0) return false
        val rec = try {
            AudioRecord(MediaRecorder.AudioSource.MIC, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, min * 4)
        } catch (e: Exception) { return false }
        if (rec.state != AudioRecord.STATE_INITIALIZED) { rec.release(); return false }
        pcm.reset()
        record = rec
        running = true
        rec.startRecording()
        thread = Thread {
            val buf = ByteArray(min)
            while (running) {
                val n = rec.read(buf, 0, buf.size)
                if (n > 0 && pcm.size() < MAX_BYTES) pcm.write(buf, 0, n)
            }
        }.also { it.start() }
        return true
    }

    /** Stop and return the recording as a WAV, or null if it was too short to be speech. */
    fun stop(): ByteArray? {
        running = false
        thread?.join(500)
        record?.let { runCatching { it.stop() }; it.release() }
        record = null
        val data = pcm.toByteArray()
        return if (data.size < RATE * 2 / 4) null else wav(data)   // under a quarter of a second
    }

    companion object {
        const val RATE = 16_000
        private const val MAX_BYTES = 60 * RATE * 2               // the PC accepts about a minute

        fun wav(pcm: ByteArray): ByteArray {
            val b = ByteBuffer.allocate(44 + pcm.size).order(ByteOrder.LITTLE_ENDIAN)
            b.put("RIFF".toByteArray()).putInt(36 + pcm.size).put("WAVE".toByteArray())
            b.put("fmt ".toByteArray()).putInt(16).putShort(1).putShort(1).putInt(RATE).putInt(RATE * 2).putShort(2).putShort(16)
            b.put("data".toByteArray()).putInt(pcm.size).put(pcm)
            return b.array()
        }
    }
}
