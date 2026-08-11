// QVariant <-> mpv_node conversion. The only non-mechanical logic in the
// bridge, so it is the only unit-tested part (see test_node_conv.cpp).
#pragma once

#include <QVariant>
#include <mpv/client.h>

// Deep-copies an mpv_node (owned by mpv) into a QVariant.
// NONE -> invalid QVariant; unknown formats degrade to invalid, never crash.
QVariant nodeToVariant(const mpv_node *node);

// Builds an mpv_node from a QVariant, owning all allocated memory for the
// lifetime of the builder (mpv_command_node / mpv_set_property copy on call).
class NodeBuilder {
public:
    explicit NodeBuilder(const QVariant &value);
    ~NodeBuilder();
    NodeBuilder(const NodeBuilder &) = delete;
    NodeBuilder &operator=(const NodeBuilder &) = delete;

    mpv_node *node() { return &node_; }

private:
    mpv_node node_;
};
