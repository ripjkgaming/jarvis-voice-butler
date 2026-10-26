# Jarvis arm and gripper: SGD300 DIY target

Updated 2026-10-04. User target: **SGD300 total**, willing to assemble, reusing the existing Jarvis laptop. Recommend a small hobby-servo arm with a simple two-finger gripper as a conditional learning build. Start with soft blocks around 10–20 g at a short 10–15 cm taught working radius; these are commissioning goals, **not verified payload/reach ratings**. Cups, phones, long reaches and arbitrary objects need a different validated design. A five-finger hand is outside this starter plan.

## Budget-fit candidate and availability

The [Adeept ADA031-V4.0 arm kit](https://www.adeept.com/adeept-arduino-compatible-diy-5-dof-robotic-arm-kit-for-arduino-uno-r3-steam-robot-arm-kit-with-arduino-and-processing-code_p0118_s0031.html) is listed at **US$69.99**. It includes acrylic structure, six AD002 servos, bearing turntable, an ATmega328P controller compatible with Arduino Uno R3 programming, Micro-USB cable and basic assembly tools. The page allows battery-free Micro-USB operation, but does not establish adequate continuous servo power or a separately switchable motor rail. Inspect the supplied board schematic first. [Version-matched manufacturer resources](https://www.adeept.com/learn/detail-64.html).

At the [Wise mid-market rate](https://wise.com/us/currency-converter/usd-to-sgd-rate) retrieved today, US$1 ≈ S$1.279, so US$69.99 ≈ **S$89.52 before shipping/tax/card fees**. Reserve S$95 for the kit. The [new V5 listing](https://www.adeept.com/robotic-arm_p0453.html) also shows US$69.99, but uses a changed controller and 2S battery/charging arrangement. Do not substitute it without checking power and pinout compatibility. The [SunFounder Uno arm](https://www.sunfounder.com/products/arduino-robot-arm-kit) is cheaper at US$47.99 but currently marked sold out.

Singapore-local stock and a delivered Singapore checkout quote are **not verified**. The manufacturer's shipping placeholder does not prove free Singapore delivery. Confirm exact version, stock, shipping, taxes and any courier handling charges before spending. Relevant Singapore GST is 9%; overseas low-value goods can be taxed at checkout depending on supplier registration and import circumstances. Reserve tax rather than assuming exemption. [IRAS rate](https://www.iras.gov.sg/taxes/goods-services-tax-%28gst%29/gst-rate-change/gst-rate-change-for-business/gst-rate-on-imports-of-low-value-goods-and-b2c-imported-non-digital-services), [IRAS import rules](https://www.iras.gov.sg/taxes/goods-services-tax-%28gst%29/consumers/gst-on-imported-low-value-goods).

## Conditional shopping envelope: exactly SGD300

These are **spending ceilings/allowances, not sourced retail prices**, except the kit conversion above. Firmware development is self-performed and not a purchased safety feature. Only proceed if actual delivered quotes fit and compatible protection can be built and verified; otherwise keep the simulator and save for the remaining hardware. Do not remove the cutoff/watchdog to meet the cap.

| Qty | Item | SGD allowance | What must be included / confirmed |
| --- | --- | ---: | --- |
| 1 kit | ADA031-V4 arm, gripper, 6 servos, controller, cable and tools | 95 | Exact V4 contents; no extra gripper or six duplicate servos. |
| 1 | Enclosed regulated low-voltage adapter | 25 | Provisionally 5 V / 5 A only if selected board/servo rail permits it; enough transient current must be measured. Singapore plug, approved enclosed adapter, correct polarity/connector. |
| 1 set | Independent latching DC emergency cutoff + normally-off motor enable | 60 | Rated low-voltage switch/button and power switching element, enclosure and connectors. Manual release must not restore motion automatically. |
| 1 set | Fuse, holder, power terminals and appropriate wire | 15 | Fuse selected against measured current and cable rating; no servo-current path through a breadboard. |
| 1 set | Rigid mounting board, clamps/bolts, padding and test blocks | 20 | Suction cups alone are not the intended stable permanent mount. |
| 1 delivery | Overseas/local shipping reserve | 25 | Placeholder; exact Singapore delivered charge required. |
| 1 reserve | GST/payment fees | 15 | Avoid adding tax twice if included in checkout price. |
| 1 optional controller | Separate USB MCU if kit board cannot isolate motor rail | 25 | Allowance for replacement/supervisor, not a requirement to buy before inspecting kit. Existing board firmware may suffice; saved money stays contingency. |
| 1 spare | Compatible micro servo / small repair allowance | 10 | Exact dimensions/horn spline/voltage; included six servos remain primary set. |
| 1 reserve | Contingency | 10 | Small buffer; unused optional-controller allowance increases it. |
| | **Total target** | **300** | Reuse laptop, existing hand tools/multimeter and desk; no camera, batteries or new printer. |

An independently reviewed or certified stop/supervisor assembly may cost more than its S$60 allowance. This table demonstrates a plausible DIY allocation, **not a confirmed purchasable complete system below S$300**. Tools not already owned (multimeter, crimper, low-voltage soldering equipment) and paid fabrication/review are extra and can break the cap. All mains stays inside a ready-made enclosed adapter; do not solder or modify mains wiring.

## Torque, power and control limitations

The [manufacturer AD002 servo listing](https://www.ww.adeept.com/adeept-ad002-servo-motor-for-rc-robot-car-compatible-with-mg90s-sevo-metal-gear_p0275.html) specifies stall torque about 2 kg·cm at 4.8 V. **Stall torque is not a usable continuous payload rating.** For illustration, 20 g at 15 cm consumes 0.3 kg·cm from the object alone; links, gripper, servo mass, friction and acceleration add torque. Two shoulder servos need coordinated motion and do not justify assuming twice the safe payload. Avoid sustained stalls; begin unloaded and stop on unusual heat/noise/current.

Six servo startup/stall currents can reset a weak controller supply. Servo current must come from the correctly rated external rail; share signal ground with the controller, but prevent USB backfeed and verify the motor cutoff actually removes all motor-power paths. The kit's USB-power claim alone is insufficient. Ordinary three-wire hobby servos do not provide measured joint position to the laptop, and commanding a closed gripper does not establish force or a successful grasp.

Before physical integration, implement and test local-controller finite joint/pulse bounds, slow speed ramps, expiring heartbeat lease, stop priority and startup/reconnect with motor enable OFF. A host Python watchdog cannot substitute for this. Provide independent cutoff and plan for the unsupported arm to drop when unpowered. Keep taught object locations and supervised operation; add position/force sensing before claiming reliable autonomous grabbing.

The [software guide](robot-arm-software.md) and existing `src/robotics/` implementation remain **mock-only**. No serial driver, controller safety firmware, physical calibration or live voice tool has been added. The demo profile has fictional three-joint limits, whereas this kit has a different servo arrangement; never copy it into the controller. Budget approval does not commission hardware.

## Higher-cost alternatives and commissioning reference

The following earlier options are retained for comparison; their USD starter allowance is **not the current SGD300 recommendation**.

Earlier comparison, 2026-10-04. Goal: a fixed tabletop arm that reaches and grabs objects. Final selection needs budget, required reach, heaviest object, object sizes, and desk dimensions. Start with a two-finger gripper and known pickup/drop locations. A five-finger hand is a later mechanical and sensing project: its mass, adapter, wiring and center of gravity reduce usable arm payload.

## Candidate platforms

| Platform | Appropriate use | Constraints and sources |
| --- | --- | --- |
| Waveshare RoArm-M2-S + supplied gripper | Low-cost, supervised learning and lightweight tabletop demonstrations | Assembled ESP32-controlled arm. Manufacturer specifies 12 V / 5 A supply, maximum workspace about 1 m diameter, and warns that power-up automatically moves joints to center. A 4DOF label does not provide the arbitrary tool orientation of a six-axis manipulator. See [manufacturer guide](https://www.waveshare.com/wiki/RoArm-M2-S). |
| Interbotix WidowX-250 6DOF / 250 S | Research setup needing wrist orientation and ROS ecosystem | Manufacturer lists 250 g working payload, with workspace and thermal/rest-pose constraints. End-tool weight and object weight must fit the applicable configuration. Do not confuse it with the newer WidowX AI. See [X-Series specifications](https://docs.trossenrobotics.com/interbotix_xsarms_docs/specifications.html). |
| UFACTORY Lite6 + compatible Gripper Lite | Higher-budget manipulation with a documented controller and physical emergency stop | Obtain a complete matched system quote and read current manual. Official installation specifies Ethernet control, 24 V factory wiring and enabling through Studio/SDK; mount securely and account for tool extension. See [installation](https://docs.lite6.ufactory.cc/2.hardware_installation.html). |

RoArm is a provisional economical candidate, not a validated autonomous or safety-rated system. Its manufacturer repository lists a 0.5 kg load at 0.5 m, but this is not a tested Jarvis operating limit. Commission initially with a soft lightweight object, conservative reach and slow motion. The repository also describes gripper-force limiting; pin and inspect the actual firmware version rather than assuming delivered firmware includes it. [Official firmware repository](https://github.com/waveshareteam/roarm_m2).

## Starter BOM: RoArm path

| Qty | Item | Compatibility / acceptance requirement |
| --- | --- | --- |
| 1 | RoArm-M2-S assembled arm, gripper and controller | Confirm exact package contents, gripper opening and tool mass before ordering; do not add loose servos or a PCA9685 for this integrated kit. |
| 1 | Manufacturer-compatible 12 V / 5 A regulated supply | Correct polarity, connector and local mains plug. Included adapter counts as this item. No lithium battery needed for the stationary prototype. |
| 1 | USB data cable to arm Type-C port | Data-capable, strain-relieved; host connector matched to existing Jarvis computer. |
| 1 | Rigid mounting plate or supplied table clamp | Secure against overturning at full intended extension; verify desk thickness, mount spacing and clearance. |
| 1 set | Independent physical latching emergency cutoff | Rated DC interruption hardware, enclosure and normally-open motor-power contactor/relay designed for actual voltage, current and inrush. An emergency button read only by Python is insufficient. Have the selected circuit reviewed before energizing. |
| 1 set | Fuse holder, suitable fuse, terminals, wiring and enclosure | Fuse chosen for supply, cable ampacity and measured startup current; insulate connections and provide strain relief. Do not guess a fuse rating from the nominal adapter label. |
| 1 | Controller-side lease/watchdog and normally-off enable path | Implement/verify on the arm controller or separate supervisor; losing host heartbeat must stop motion without host cooperation. Stock support is unverified. |
| 1 set | Workspace markers, soft test blocks and padded catch tray | Keeps commissioning repeatable; cutting torque can let the arm fall or drop its object. |
| 1 | Existing Jarvis host | Run local device gateway on the computer physically connected to the arm. A cloud container cannot directly access its USB port. |
| Optional 1 | Fixed USB camera and calibration target | Needed for later visual object localization; begin with taught locations to avoid camera/grasp-calibration complexity. |

Planning allowance, **USD estimates, not supplier quotes**: arm kit roughly $180–300; mounting/power/cabling roughly $50–150; independent cutoff, enclosure, fuse and supervision roughly $100–300; optional camera/calibration roughly $30–150. Combined starter allowance roughly $330–750 without a new computer, shipping, taxes or fabrication labor. Only the kit range is anchored to the [current manufacturer listing](https://www.waveshare.com/product/robotics/robot-arm-control/roarm-m2-s.htm), which shows $179.99–299.99 across S/Pro options, not a guaranteed S configuration price. More expensive six-axis systems require a current complete-system quote.

## Control contract and commissioning

Use Jarvis intent → deterministic local gateway → bounded controller commands. The model chooses named, taught tasks; it must not emit raw servo values. RoArm's documented wired interface is newline-delimited JSON at 115200 baud over USB serial, with bidirectional responses. A project-specific guarded protocol cannot be sent directly to stock RoArm firmware: it needs a reviewed adapter and controller changes. [Official serial example](https://www.waveshare.com/wiki/RoArm-M2-S_Python_Serial_Communication_Control), [command reference](https://www.waveshare.com/wiki/RoArm-M2-S_JSON_Command_Meaning).

Before enabling any Jarvis motion:

1. Confirm model, firmware revision, mounting, supply polarity and cutoff wiring. Record the physical travel envelope, including power-up motion, gripper and carried object.
2. Test independent cutoff with no object and support the arm against collapse. Releasing the button must not automatically restart a task; require deliberate local re-arm.
3. Verify controller-enforced joint bounds, velocity/acceleration limits, stale-feedback rejection, current/temperature protection and heartbeat expiry. A watchdog that resets into automatic centering is not an acceptable stop strategy.
4. Test cable removal, host process termination, duplicate/malformed commands, controller reboot and supply recovery. Motion must not resume from queued or persisted commands.
5. Teach collision-free pickup, transit and drop poses. Reject unreachable poses, unknown objects and routes crossing people, screens or the table. Joint encoder feedback alone does not establish obstacle clearance or a successful grasp.
6. Demonstrate repeated slow picks with soft test blocks, then measure practical payload and grasp reliability before adding vision or increasing load. Keep a reachable physical cutoff and a clear workspace.

For Lite6, its manual distinguishes control-box emergency stop (Category 1) from safeguard stop (Category 2); use the manufacturer's interfaces and installation procedure rather than transplanting the hobby-arm cutoff design. [Stop categories and power isolation](https://docs.lite6.ufactory.cc/8.production_information.html).

Open decisions: exact delivered SGD300 cart and Singapore stock; desired reach; maximum object mass and dimensions; free desk area; fixed pickup locations versus camera-guided grabs; whether a humanlike five-finger hand is essential.
