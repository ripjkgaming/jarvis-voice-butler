package dev.jarvis.link

import android.graphics.BitmapFactory
import android.os.Bundle
import android.util.Base64
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ImageView
import android.widget.TextView
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import java.io.File

/** See-what-I-see: preview, capture to the PC, view the latest frame. */
class CameraFragment : Fragment() {
    private var capture: ImageCapture? = null
    private lateinit var status: TextView
    private lateinit var preview: PreviewView
    private lateinit var latest: ImageView

    override fun onCreateView(inflater: LayoutInflater, host: ViewGroup?, state: Bundle?): View {
        val v = inflater.inflate(R.layout.fragment_camera, host, false)
        preview = v.findViewById(R.id.preview)
        latest = v.findViewById(R.id.img_latest)
        status = v.findViewById(R.id.txt_cam_status)
        startPreview()
        v.findViewById<Button>(R.id.btn_capture).setOnClickListener { takePhoto() }
        v.findViewById<Button>(R.id.btn_latest).setOnClickListener { loadLatest() }
        return v
    }

    private fun startPreview() {
        val future = ProcessCameraProvider.getInstance(requireContext())
        future.addListener({
            try {
                val provider = future.get()
                provider.unbindAll()
                provider.bindToLifecycle(
                    this, CameraSelector.DEFAULT_BACK_CAMERA,
                    Preview.Builder().build().also { it.setSurfaceProvider(preview.surfaceProvider) },
                    ImageCapture.Builder().setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                        .build().also { capture = it },
                )
            } catch (e: Exception) {
                status.text = "Camera unavailable: ${e.message}"
            }
        }, ContextCompat.getMainExecutor(requireContext()))
    }

    private fun takePhoto() {
        val cap = capture ?: return
        status.text = "Capturing…"
        val file = File(requireContext().cacheDir, "jarvis_frame.jpg")
        cap.takePicture(
            ImageCapture.OutputFileOptions.Builder(file).build(),
            ContextCompat.getMainExecutor(requireContext()),
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(out: ImageCapture.OutputFileResults) {
                    Thread {
                        try {
                            val b64 = Base64.encodeToString(file.readBytes(), Base64.NO_WRAP)
                            val r = LinkApi(Prefs(requireContext())).cameraFrame(b64)
                            activity?.runOnUiThread {
                                status.text = if (r.optBoolean("ok")) "Sent to Jarvis" else "Failed"
                            }
                        } catch (e: Exception) {
                            activity?.runOnUiThread { status.text = "Error: ${e.message}" }
                        }
                    }.also { it.isDaemon = true; it.start() }
                }

                override fun onError(e: ImageCaptureException) {
                    status.text = "Capture failed: ${e.message}"
                }
            },
        )
    }

    private fun loadLatest() {
        status.text = "Loading…"
        Thread {
            try {
                val bytes = LinkApi(Prefs(requireContext())).cameraLatest()
                val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                activity?.runOnUiThread {
                    latest.setImageBitmap(bmp)
                    status.text = "Latest frame"
                }
            } catch (e: Exception) {
                activity?.runOnUiThread { status.text = "Error: ${e.message}" }
            }
        }.also { it.isDaemon = true; it.start() }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        try {
            ProcessCameraProvider.getInstance(requireContext()).get().unbindAll()
        } catch (_: Exception) { }
    }
}
