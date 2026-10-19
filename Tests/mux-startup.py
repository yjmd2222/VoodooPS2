#!/usr/bin/env python
"""Compile the actual startup probe methods against scripted PS/2 I/O.

Runs with the system Python on High Sierra. Requires clang++ in PATH.
This checks protocol/routing decisions, not real controller timing or IOKit.
"""
from __future__ import print_function
import os
import shutil
import subprocess
import tempfile

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(root, "VoodooPS2Controller", "VoodooPS2Controller.cpp")) as f:
    source = f.read()


def method(signature):
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


start = source.index("  if (_resetControllerFlag & RESET_CONTROLLER_ON_BOOT) {",
                     source.index("// Reset attached devices"))
end = source.index("  // Use a spin lock", start)
startup = source[start:end].rsplit("  //", 1)[0]

harness = r'''
#include <cassert>
#include <cstdio>
#include <deque>
#include <utility>
#include <vector>
using UInt8 = unsigned char;
enum { kPS2KbdIdx=0, kPS2AuxIdx=1, kPS2AuxMaxIdx=2, PS2_MUX_PORTS=4,
       kDP_SetDefaultsAndDisable=0xf5, kDP_GetId=0xf2, kSC_Acknowledge=0xfa,
       kCP_TransmitToMuxedMouse=0x90, kCP_TransmitToMouse=0xd4,
       RESET_CONTROLLER_ON_BOOT=1 };
void IOLog(const char *, ...) {}
struct ApplePS2Controller {
    bool _kbdOnly=false, _muxPresent=true;
    size_t _nubsCount=5;
    int _resetControllerFlag=1;
    std::deque<std::pair<size_t, UInt8>> responses;
    std::deque<bool> muxResults;
    std::vector<bool> muxChanges;
    std::vector<UInt8> routes, commands;
    const char *getName() { return "test"; }
    void writeCommandPort(UInt8 c) { routes.push_back(c); }
    void writeDataPort(UInt8 c) { commands.push_back(c); }
    UInt8 readDataPort(size_t p) {
        assert(!responses.empty());
        auto response=responses.front(); responses.pop_front();
        assert(response.first==p); return response.second;
    }
    bool setMuxMode(bool enabled) {
        muxChanges.push_back(enabled); assert(!muxResults.empty());
        bool ok=muxResults.front(); muxResults.pop_front(); return ok;
    }
    void flushDataPort() {}
    bool resetDevices();
    bool probeAuxiliaryDevices(UInt8 command);
    void retryAuxiliaryInLegacyMode();
    void startup();
    void response(size_t port, UInt8 byte) { responses.emplace_back(port,byte); }
    void failedMux(bool reset=true) {
        if (reset) response(0,0xfa);
        for (size_t i=1;i<=4;++i) response(i,0);
    }
    void complete() { assert(responses.empty() && muxResults.empty()); }
};
'''
harness += "\n".join(method(s) for s in [
    "bool ApplePS2Controller::resetDevices()",
    "bool ApplePS2Controller::probeAuxiliaryDevices(UInt8 command)",
    "void ApplePS2Controller::retryAuxiliaryInLegacyMode()"])
harness += "\nvoid ApplePS2Controller::startup() {\n" + startup + "\n}\n"
harness += r'''
int main() {
    { // Every mux port fails: disable mux, retry legacy once, create two nubs.
        ApplePS2Controller c; c.failedMux(); c.muxResults={true};
        c.response(1,0xfa); c.startup(); c.complete();
        assert(!c._muxPresent && c._nubsCount==2);
        assert(c.muxChanges==std::vector<bool>{false});
        assert((c.routes==std::vector<UInt8>{0x90,0x91,0x92,0x93,0xd4}));
    }
    { // Any healthy mux port preserves all four ports.
        ApplePS2Controller c; c.response(0,0xfa);
        for (size_t i=1;i<=4;++i) c.response(i,i==3?0xfa:0);
        c.startup(); c.complete();
        assert(c._muxPresent && c._nubsCount==5 && c.muxChanges.empty());
    }
    { // A failed legacy retry must not loop or re-enable mux.
        ApplePS2Controller c; c.failedMux(); c.muxResults={true};
        c.response(1,0); c.startup(); c.complete();
        assert(!c._muxPresent && c._nubsCount==2 && c.muxChanges.size()==1);
    }
    { // Failed disable: restore mux, do not assume legacy routing.
        ApplePS2Controller c; c.failedMux(); c.muxResults={false,true};
        c.startup(); c.complete();
        assert(c._muxPresent && c._nubsCount==5);
        assert((c.muxChanges==std::vector<bool>{false,true}));
    }
    { // Even failed restoration is bounded; no legacy commands are sent.
        ApplePS2Controller c; c.failedMux(); c.muxResults={false,false};
        c.startup(); c.complete();
        assert(c._muxPresent && c._nubsCount==5 && c.routes.size()==4);
    }
    { // ps2rst=0: use Get ID, consume its response, never reset keyboard.
        ApplePS2Controller c; c._resetControllerFlag=0; c.failedMux(false);
        c.muxResults={true}; c.response(1,0xfa); c.response(1,0);
        c.startup(); c.complete();
        assert(!c._muxPresent && c.commands.size()==5);
        for (auto command:c.commands) assert(command==0xf2);
    }
    { // Healthy Get ID: consume ID bytes, including ACK-looking ID data.
        ApplePS2Controller c; c._resetControllerFlag=0;
        for (size_t i=1;i<=4;++i) {c.response(i,0xfa);c.response(i,0xfa);}
        c.startup(); c.complete(); assert(c._muxPresent && c.muxChanges.empty());
    }
    { // Handshake-failed/ps2nomux paths remain legacy without another toggle.
        ApplePS2Controller c; c._muxPresent=false; c._nubsCount=2;
        c.response(0,0xfa); c.response(1,0xfa); c.startup(); c.complete();
        assert((c.routes==std::vector<UInt8>{0xd4}) && c.muxChanges.empty());
    }
    { // Keyboard-only: no auxiliary command or mux toggle.
        ApplePS2Controller c; c._kbdOnly=true; c._muxPresent=false;c._nubsCount=1;
        c.response(0,0xfa);c.startup();c.complete();
        assert(c.routes.empty() && c.muxChanges.empty());
    }
    { // Explicit no-reset plus legacy/keyboard-only sends no startup probes.
        ApplePS2Controller c; c._resetControllerFlag=0;c._muxPresent=false;
        c.startup();c.complete();assert(c.commands.empty());
    }
    { // RESEND is not a successful acknowledgement.
        ApplePS2Controller c;c.response(0,0xfa);
        for (size_t i=1;i<=4;++i)c.response(i,0xfe);
        c.muxResults={true};c.response(1,0xfa);c.startup();c.complete();
        assert(!c._muxPresent);
    }
    std::puts("11 mux startup scenarios passed");
}
'''

work = tempfile.mkdtemp(prefix="voodoops2-mux-test-")
try:
    cpp = os.path.join(work, "mux.cpp")
    binary = os.path.join(work, "mux-test")
    with open(cpp, "w") as f:
        f.write(harness)
    subprocess.check_call(["clang++", "-std=c++14", "-Wall", "-Wextra", "-Werror", cpp, "-o", binary])
    subprocess.check_call([binary])
finally:
    shutil.rmtree(work)
