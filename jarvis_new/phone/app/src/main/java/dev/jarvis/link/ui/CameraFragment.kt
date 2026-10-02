package dev.jarvis.link.ui

import android.Manifest
import android.graphics.BitmapFactory
import android.os.Bundle
import android.view.View
import android.widget.ImageView
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import dev.jarvis.link.R
import java.io.File

/** See-what-I-see: live preview, send a frame to the PC, view the latest frame back. */
class CameraFragment : Fragment(R.layout.fragment_camera) {
    private var capture: ImageCapture? = null

    override fun onViewCreated(v: View, state: Bundle?) {
        val m = graph.camera
        v.onClick(R.id.camera_btn_capture) { takePhoto() }
        v.onClick(R.id.camera_btn_latest) { m.loadLatest() }
        render(m.state) { s ->
            v.text(R.id.camera_notice).text = s.notice
            v.button(R.id.camera_btn_capture).isEnabled = !s.busy
            s.latest?.let {
                v.findViewById<ImageView>(R.id.camera_image)
                    .setImageBitmap(BitmapFactory.decodeByteArray(it, 0, it.size))
            }
        }
        (requireActivity() as MainActivity).requirePermissions(arrayOf(Manifest.permission.CAMERA)) { ok ->
            if (ok && isAdded) startPreview(v) else if (isAdded) m.note("Camera permission denied.")
        }
    }

    private fun startPreview(v: View) {
        val future = ProcessCameraProvider.getInstance(requireContext())
        future.addListener({
            if (!isAdded || view == null) return@addListener
            try {
                val provider = future.get()
                provider.unbindAll()
                val cap = ImageCapture.Builder().setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY).build()
                provider.bindToLifecycle(
                    viewLifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA,
                    Preview.Builder().build().also { it.setSurfaceProvider(v.findViewById<PreviewView>(R.id.camera_preview).surfaceProvider) },
                    cap,
                )
                capture = cap
            } catch (e: Exception) {
                graph.camera.note("Camera unavailable: ${e.message}")
            }
        }, ContextCompat.getMainExecutor(requireContext()))
    }

    private fun takePhoto() {
        val cap = capture ?: run { graph.camera.note("Camera is not ready."); return }
        val file = File(requireContext().cacheDir, "jarvis_frame.jpg")
        graph.camera.note("Capturing...")
        cap.takePicture(
            ImageCapture.OutputFileOptions.Builder(file).build(),
            ContextCompat.getMainExecutor(requireContext()),
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(out: ImageCapture.OutputFileResults) {
                    graph.camera.upload(file.readBytes())
                    file.delete()
                }

                override fun onError(e: ImageCaptureException) {
                    graph.camera.note("Capture failed: ${e.message}")
                }
            },
        )
    }

    override fun onDestroyView() {
        capture = null
        super.onDestroyView()
    }
}
