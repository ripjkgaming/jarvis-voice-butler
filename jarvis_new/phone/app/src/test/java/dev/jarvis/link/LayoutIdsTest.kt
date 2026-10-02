package dev.jarvis.link

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The restyle maps onto view ids, so they must stay stable and follow the
 * `<screen>_<role>` convention. This test reads the layout XML directly.
 */
class LayoutIdsTest {
    private val layoutDir: File = listOf("src/main/res/layout", "app/src/main/res/layout", "phone/app/src/main/res/layout")
        .map { File(it) }.first { it.isDirectory }

    private fun ids(file: String): Set<String> =
        Regex("@\\+id/([A-Za-z0-9_]+)").findAll(File(layoutDir, file).readText()).map { it.groupValues[1] }.toSet()

    private val expected = mapOf(
        "fragment_home.xml" to "home_",
        "fragment_voice.xml" to "voice_",
        "fragment_chat.xml" to "chat_",
        "fragment_control.xml" to "control_",
        "fragment_screens.xml" to "screens_",
        "fragment_activity.xml" to "activity_",
        "fragment_phone.xml" to "phone_",
        "fragment_camera.xml" to "camera_",
        "fragment_approvals.xml" to "approvals_",
        "fragment_alerts.xml" to "alerts_",
        "fragment_settings.xml" to "settings_",
        "session_assistant.xml" to "session_",
        "activity_oneshot.xml" to "oneshot_",
        "activity_main.xml" to "main_",
    )

    @Test
    fun everyIdIsPrefixedWithItsScreen() {
        for ((file, prefix) in expected) {
            val found = ids(file)
            assertTrue("$file has no ids", found.isNotEmpty())
            for (id in found) assertTrue("$id in $file must start with $prefix", id.startsWith(prefix))
        }
    }

    @Test
    fun idsAreUniqueAcrossAllLayouts() {
        val all = expected.keys.flatMap { ids(it).toList() }
        assertEquals(all.size, all.toSet().size)
    }

    @Test
    fun everyButtonHasAnIdAndLabel() {
        for (file in expected.keys) {
            val xml = File(layoutDir, file).readText()
            for (m in Regex("<Button\\b[^>]*>", RegexOption.DOT_MATCHES_ALL).findAll(xml)) {
                assertTrue("Button without id in $file: ${m.value}", m.value.contains("android:id=\"@+id/"))
                assertTrue("Button without text in $file", m.value.contains("android:text=\""))
            }
        }
    }

    @Test
    fun noCustomViewsOrStylingInLayouts() {
        for (file in expected.keys) {
            val xml = File(layoutDir, file).readText()
            assertTrue("$file uses a style", !xml.contains("style=\""))
            assertTrue("$file hard-codes colors", !Regex("#[0-9A-Fa-f]{6,8}").containsMatchIn(xml))
        }
    }
}
