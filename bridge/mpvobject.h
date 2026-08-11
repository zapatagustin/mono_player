// The single mpv facade. Owns the mpv_handle; Python and QML drive mpv only
// through this item's invokables and signals (see GUIDELINE.org, Rendering).
#pragma once

#include <QQuickFramebufferObject>
#include <QVariant>
#include <QtQml/qqmlregistration.h>
#include <mpv/client.h>
#include <mpv/render_gl.h>

class MpvObject : public QQuickFramebufferObject
{
    Q_OBJECT
    QML_ELEMENT

public:
    explicit MpvObject(QQuickItem *parent = nullptr);
    ~MpvObject() override;

    Renderer *createRenderer() const override;

    Q_INVOKABLE void command(const QVariant &params);
    Q_INVOKABLE void setProperty(const QString &name, const QVariant &value);
    Q_INVOKABLE QVariant getProperty(const QString &name) const;

signals:
    void playlistPosChanged(qint64 pos);
    void playbackTimeChanged(double secs);
    void logMessage(QString prefix, QString level, QString text);

private slots:
    void doUpdate();
    void onMpvEvents();

private:
    friend class MpvRenderer;
    static void onMpvRedraw(void *ctx);
    static void wakeup(void *ctx);
    void handleEvent(mpv_event *event);

    mpv_handle *mpv = nullptr;
    mutable mpv_render_context *mpv_gl = nullptr;
};
