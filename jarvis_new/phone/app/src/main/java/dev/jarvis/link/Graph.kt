package dev.jarvis.link

import android.app.Application
import dev.jarvis.link.device.Audio16k
import dev.jarvis.link.device.AndroidBattery
import dev.jarvis.link.device.AndroidLocation
import dev.jarvis.link.device.LiveKitEngine
import dev.jarvis.link.device.RemoteLauncher
import dev.jarvis.link.device.TtsManager
import dev.jarvis.link.logic.ActivityModel
import dev.jarvis.link.logic.ApprovalStore
import dev.jarvis.link.logic.CallHost
import dev.jarvis.link.logic.CallModel
import dev.jarvis.link.logic.CameraModel
import dev.jarvis.link.logic.ChatModel
import dev.jarvis.link.logic.ControlModel
import dev.jarvis.link.logic.Conversation
import dev.jarvis.link.logic.HomeModel
import dev.jarvis.link.logic.Messenger
import dev.jarvis.link.logic.PcActions
import dev.jarvis.link.logic.PhoneModel
import dev.jarvis.link.logic.Prefs
import dev.jarvis.link.logic.ScreensModel
import dev.jarvis.link.logic.SettingsModel
import dev.jarvis.link.logic.SosModel
import dev.jarvis.link.logic.TalkService
import dev.jarvis.link.logic.TelemetryReporter
import dev.jarvis.link.net.Bridge
import dev.jarvis.link.net.BridgeClient
import dev.jarvis.link.svc.AndroidApprovalNotifier
import dev.jarvis.link.svc.AndroidServiceControl
import dev.jarvis.link.svc.AndroidSosEffects
import dev.jarvis.link.svc.VoiceCallService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

/**
 * Process-wide object graph. State holders live here (not in fragments) so
 * rotation, tab switches and background/foreground never lose a call, a
 * recording or a half-typed conversation. Process death rebuilds it from
 * [Prefs] (settings + conversation are persisted).
 */
class Graph(private val app: Application) {
    val prefs = Prefs(SharedPrefsStore(app))
    val config = { prefs.config() }
    val bridge: Bridge = BridgeClient(config)
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val io = Dispatchers.IO

    val tts = TtsManager(app, prefs)
    val conversation = Conversation(prefs)
    val recorder = Audio16k.Recorder(app)

    val actions = PcActions(bridge, config) { host, port -> RemoteLauncher.open(JarvisApp.foreground() ?: app, host, port) }
    val messenger = Messenger(bridge, actions, conversation, tts)
    val talkService = TalkService(bridge, actions, conversation, tts, Audio16k.Player, config)
    val approvals = ApprovalStore(AndroidApprovalNotifier(app))
    val telemetry = TelemetryReporter(bridge, config, { AndroidBattery.sample(app) }, scope, io)
    val services = AndroidServiceControl(app, prefs, tts)

    val home by lazy {
        HomeModel(config, actions, messenger, talkService, recorder, conversation, { AndroidBattery.sample(app) }, scope, io)
    }
    val chat by lazy { ChatModel(messenger, conversation, scope, io) }
    val control by lazy { ControlModel(config, actions, scope, io) }
    val screens by lazy { ScreensModel(config, actions, scope, io) }
    val activity by lazy { ActivityModel(bridge, scope, io) }
    val phone by lazy { PhoneModel(actions, scope, io) }
    val camera by lazy { CameraModel(bridge, scope, io, dev.jarvis.link.device.JpegShrinker::shrink) }
    val sos by lazy {
        SosModel(config, actions, AndroidSosEffects(app), { AndroidLocation.lastKnown(app) }, tts, scope, io)
    }
    val settings by lazy { SettingsModel(prefs, actions, services, scope, io) }
    val call by lazy {
        CallModel(
            bridge, config, LiveKitEngine(app),
            object : CallHost {
                override fun onCallStarting() = VoiceCallService.start(app)
                override fun onCallEnded() = VoiceCallService.stop(app)
            },
            messenger, scope, io,
        )
    }
}
