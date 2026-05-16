package com.denzhogzhuy.xiaorobot

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import kotlin.math.hypot
import kotlin.math.min

class JoystickView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    var onMove: ((normX: Float, normY: Float) -> Unit)? = null
    var onRelease: (() -> Unit)? = null

    private val basePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFF30363D.toInt()
        style = Paint.Style.FILL
    }
    private val knobPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFF58A6FF.toInt()
        style = Paint.Style.FILL
    }
    private var knobX = 0f
    private var knobY = 0f
    private var centerX = 0f
    private var centerY = 0f
    private var radius = 0f

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        centerX = w / 2f
        centerY = h / 2f
        radius = min(w, h) * 0.38f
        knobX = centerX
        knobY = centerY
    }

    override fun onDraw(canvas: Canvas) {
        canvas.drawCircle(centerX, centerY, radius, basePaint)
        canvas.drawCircle(knobX, knobY, radius * 0.28f, knobPaint)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_MOVE -> {
                val dx = event.x - centerX
                val dy = event.y - centerY
                val d = hypot(dx.toDouble(), dy.toDouble()).toFloat()
                val scale = if (d > radius) radius / d else 1f
                knobX = centerX + dx * scale
                knobY = centerY + dy * scale
                val nx = (knobX - centerX) / radius
                val ny = -(knobY - centerY) / radius
                onMove?.invoke(nx.coerceIn(-1f, 1f), ny.coerceIn(-1f, 1f))
                invalidate()
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                knobX = centerX
                knobY = centerY
                onRelease?.invoke()
                invalidate()
            }
        }
        return true
    }
}
