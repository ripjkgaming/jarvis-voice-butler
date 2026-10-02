package dev.jarvis.link.logic

import dev.jarvis.link.net.Ack
import dev.jarvis.link.net.BatterySample
import dev.jarvis.link.net.Bridge
import dev.jarvis.link.net.BridgeConfig
import dev.jarvis.link.net.BridgeError
import dev.jarvis.link.net.BridgeException
import dev.jarvis.link.net.CallToken
import dev.jarvis.link.net.Caption
import dev.jarvis.link.net.ChatReply
import dev.jarvis.link.net.RoomInfo
import dev.jarvis.link.net.RouteReply
import dev.jarvis.link.net.TalkReply
import dev.jarvis.link.net.ToolResult
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.test.TestCoroutineScheduler
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import org.json.JSONObject

fun tr(ok: Boolean = true, vararg f: Pair<String, Any?>, error: String? = null): ToolResult {
    val o = JSONObject().put("ok", ok)
    f.forEach { o.put(it.first, it.second) }
    if (error != null) o.put("error", error)
    return ToolResult(ok, error, o)
}

fun bridgeFail(e: BridgeError): Nothing = throw BridgeException(e)

/** Scriptable [Bridge]: every endpoint is a replaceable lambda; calls are recorded. */
class FakeBridge : Bridge {
    val calls = mutableListOf<String>()
    val toolCalls = mutableListOf<Pair<String, JSONObject>>()
    val chatCalls = mutableListOf<Triple<String, List<List<String>>, Boolean>>()
    val typed = mutableListOf<String>()
    val keys = mutableListOf<String>()
    val telemetry = mutableListOf<BatterySample>()
    val frames = mutableListOf<ByteArray>()
    var talkAudio: String? = null

    var onHealth: () -> JSONObject = { JSONObject().put("version", "1.0").put("uptime_s", 7200) }
    var onStatus: () -> JSONObject = { JSONObject().put("agent_name", "jarvis").put("livekit_configured", true) }
    var onCaptions: (Int) -> List<Caption> = { emptyList() }
    var onRoom: () -> RoomInfo = { RoomInfo(null, false, false) }
    var onSummon: (String?) -> Ack = { Ack(true, null) }
    var onToken: (String, Boolean) -> CallToken = { _, _ -> CallToken("wss://lk", "tok") }
    var onTool: (String, JSONObject) -> ToolResult = { _, _ -> tr() }
    var onRoute: (String) -> RouteReply? = { null }
    var onTalk: () -> TalkReply = { TalkReply("hello", "hi there", "", 22050, null, null) }
    var onChat: (String, List<List<String>>, Boolean) -> ChatReply = { _, _, _ -> ChatReply("ok", null, false, null) }
    var onType: (String) -> Unit = {}
    var onKey: (String) -> Unit = {}
    var onMicMuted: () -> Boolean? = { false }
    var onSetMic: (Boolean) -> Ack = { Ack(true, null) }
    var onActions: () -> List<String> = { emptyList() }
    var onSys: () -> JSONObject = { JSONObject() }
    var onFrame: (ByteArray) -> Unit = {}
    var onLatest: () -> ByteArray? = { null }
    var onTelemetry: (BatterySample) -> Unit = {}

    override fun health() = run { calls += "health"; onHealth() }
    override fun status() = run { calls += "status"; onStatus() }
    override fun captions(limit: Int) = run { calls += "captions"; onCaptions(limit) }
    override fun room() = run { calls += "room"; onRoom() }
    override fun summon(text: String?) = run { calls += "summon"; onSummon(text) }
    override fun token(room: String, dispatch: Boolean) = run { calls += "token:$room:$dispatch"; onToken(room, dispatch) }
    override fun typeText(text: String) { calls += "type"; typed += text; onType(text) }
    override fun pressKey(key: String) { calls += "key"; keys += key; onKey(key) }
    override fun tool(name: String, args: JSONObject) = run { calls += "tool:$name"; toolCalls += name to args; onTool(name, args) }
    override fun route(text: String) = run { calls += "route"; onRoute(text) }
    override fun talk(audioB64: String, rate: Int) = run { calls += "talk"; talkAudio = audioB64; onTalk() }
    override fun chat(text: String, history: List<List<String>>, voice: Boolean) =
        run { calls += "chat"; chatCalls += Triple(text, history, voice); onChat(text, history, voice) }
    override fun cameraFrame(jpeg: ByteArray) { calls += "frame"; frames += jpeg; onFrame(jpeg) }
    override fun cameraLatest() = run { calls += "latest"; onLatest() }
    override fun micMuted() = run { calls += "mic"; onMicMuted() }
    override fun setMicMuted(muted: Boolean) = run { calls += "setmic:$muted"; onSetMic(muted) }
    override fun actions(limit: Int) = run { calls += "actions"; onActions() }
    override fun sys() = run { calls += "sys"; onSys() }
    override fun postTelemetry(sample: BatterySample) { calls += "telemetry"; telemetry += sample; onTelemetry(sample) }
}

class RecordingSpeaker : Speaker {
    val said = mutableListOf<String>()
    override fun speak(text: String) { said += text }
}

class RecordingPlayer : AudioPlayer {
    val played = mutableListOf<Pair<String, Int>>()
    var fail = false
    override fun play(audioB64: String, rate: Int) {
        if (fail) throw IllegalStateException("no audio device")
        played += audioB64 to rate
    }
}

class FakeCapture(var canStart: Boolean = true, var pcm: ByteArray = ByteArray(32_000)) : AudioCapture {
    var started = 0
    var stopped = 0
    override fun start(): Boolean { started++; return canStart }
    override fun stop(): ByteArray { stopped++; return pcm }
}

/** Mutable settings for tests. */
class TestEnv {
    val prefs = Prefs(MemoryStore()).also { it.host = "100.1.2.3"; it.token = "tok" }
    val config: () -> BridgeConfig = { prefs.config() }
    val scheduler = TestCoroutineScheduler()
    @OptIn(ExperimentalCoroutinesApi::class)
    val dispatcher = UnconfinedTestDispatcher(scheduler)
    val scope = CoroutineScope(SupervisorJob() + dispatcher)
    val bridge = FakeBridge()
    val speaker = RecordingSpeaker()
    val player = RecordingPlayer()
    val remote = mutableListOf<Pair<String?, Int?>>()
    val actions = PcActions(bridge, config) { h, p -> remote += h to p }
    val conversation = Conversation(prefs, clock = { 1000L })
    val messenger = Messenger(bridge, actions, conversation, speaker)
    val talk = TalkService(bridge, actions, conversation, speaker, player, config)

    fun advance(ms: Long) { scheduler.advanceTimeBy(ms); scheduler.runCurrent() }
}
