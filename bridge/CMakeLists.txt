cmake_minimum_required(VERSION 3.21)
project(mpvbridge LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Quick)
find_package(PkgConfig REQUIRED)
pkg_check_modules(MPV REQUIRED IMPORTED_TARGET mpv)

qt_add_qml_module(mpvbridge
    URI MpvBridge
    VERSION 1.0
    PLUGIN_TARGET mpvbridgeplugin
    OUTPUT_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}/MpvBridge
    SOURCES
        mpvobject.cpp mpvobject.h
        node_conv.cpp node_conv.h
)
target_link_libraries(mpvbridge PRIVATE Qt6::Quick PkgConfig::MPV)

enable_testing()
add_executable(test_node_conv test_node_conv.cpp node_conv.cpp)
target_link_libraries(test_node_conv PRIVATE Qt6::Core PkgConfig::MPV)
add_test(NAME node_conv COMMAND test_node_conv)
