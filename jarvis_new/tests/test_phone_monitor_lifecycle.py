"""Native wiring keeps the opted-in incoming monitor independent of mic access."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "phone/app/src/main/java/dev/jarvis/link"


def test_boot_restores_incoming_monitor_without_microphone_features():
    source = (ROOT / "svc/Services.kt").read_text()
    boot = source.split("class BootReceiver", 1)[1]
    assert "if (!graph.prefs.autostart) return" in boot
    assert "if (graph.prefs.incomingCalls) IncomingCallService.start(context)" in boot
    assert boot.index("IncomingCallService.start(context)") < boot.index(
        "if (!graph.prefs.micUplink"
    )


def test_monitor_system_restart_remains_opt_in():
    source = (ROOT / "svc/IncomingCalls.kt").read_text()
    service = source.split("class IncomingCallService", 1)[1]
    start = service.split("override fun onStartCommand", 1)[1].split(
        "override fun onDestroy", 1
    )[0]
    assert "if (!JarvisApp.graph(this).prefs.incomingCalls)" in start
    assert start.index("prefs.incomingCalls)") < start.index("startForeground")
    assert "return START_STICKY" in start
