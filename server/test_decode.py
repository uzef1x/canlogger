"""Checks the polled-reply decoders against the example frames printed in the
"Nissan Leaf 2018" polling list (dalathegreat/leaf_can_bus_messages). Run: python3 server/test_decode.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decode


def run(frames):
    st = decode.Polled()
    st.feed([(i + 1, cid, bytes.fromhex(h), 0.0) for i, (cid, h) in enumerate(frames)])
    return {x["name"]: x["value"] for x in decode.polled(st)}


def check(got, name, want):
    assert got.get(name) == want, f"{name}: got {got.get(name)!r}, want {want!r}"


g = run([(0x79A, "0562130480FE0000"), (0x79A, "0462115601000000"), (0x79A, "04621103A3000000"),
         (0x79A, "05621183FF720000")])
check(g, "Power switch", "On"); check(g, "Gear", "Park")
check(g, "12 V battery (VCM)", 13.04); check(g, "12 V battery current", -0.55)
g = run([(0x763, "06620E010004DB00")])
check(g, "Odometer", 1243)
g = run([(0x7BB, "101F6104020D1302"), (0x7BB, "210314FFFFFF020B"), (0x7BB, "22131300FFFFFFFF"),
         (0x7BB, "2300000000000000"), (0x7BB, "2400000000000000")])
check(g, "Pack temperature 1", 19.2)
g = run([(0x7BB, "101A610614555551"), (0x7BB, "215055412B565415"), (0x7BB, "2251FFFFFFFFFFFF"),
         (0x7BB, "23FFFFFFFFFFFFFF")])
check(g, "Balancing cells", 38)
g = run([(0x7BB, "1016618432333055"), (0x7BB, "214B313139324530"), (0x7BB, "2230303134383220"),
         (0x7BB, "23A0000000000000")])
check(g, "Battery serial", "230UK1192E0001482")
print("ok")
