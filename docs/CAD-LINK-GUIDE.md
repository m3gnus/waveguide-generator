# Finish a speaker in Fusion and solve it in WG

Use WG to design the waveguide, add the rest of the speaker in Autodesk Fusion,
then send the finished geometry back for an acoustic solve. You can also start
with a model drawn entirely in Fusion. WG solves the geometry you send; CAD
edits do not turn into editable waveguide parameters in WG.

This guide describes the current WG and its bundled WGLink add-in. Fusion runs
on Windows and macOS. See the [general user guide](USER-GUIDE.md) for launching
WG and using the results viewer.

## 1. Install and enable WGLink

1. Install Fusion and WG on the same computer. Close Fusion during add-in
   installation or replacement.
2. On Windows, select **Install the WGLink add-in for Autodesk Fusion** in WG's
   installer. The task is preselected when Fusion's AddIns folder exists;
   a silent install must opt in with `/TASKS="wglink"`. Setup reports whether
   WGLink was installed, updated, not selected, not detected, preserved or
   failed, and records the outcome in its setup log.
   WG installs and updates the add-in at startup only if you chose it in setup,
   installed it from WG, or have received a model from Fusion through the link.
   It also keeps updating a copy this installation already owns. Otherwise,
   use **Install add-in** in the **CAD Link** panel, with Fusion closed. This
   action also repairs the copy and is available before a folder is selected.
   Merely opening the panel, opening or uploading a `.wgreturn` by hand,
   exporting from WG, or having link metadata in a design file does not count.
   No separate scientific Python installation is needed for the packaged copy.
   For a source installation, use WG's installer; the add-in packaging details
   are in the [WGLink integration notes](../integrations/wglink/README.md).
3. Start Fusion. Under **Utilities → Scripts and Add-Ins → Add-Ins**, select
   **WGLink**, run it if needed, and tick **Run on Startup**. Its commands appear
   in the **WGLink** panel under **Utilities**. Keep only one WGLink registration.
4. In WG, open **Settings → WG root folder**. The default is
   `Documents/Waveguide Generator`: its `cadlink/` child is the **WGLink folder**,
   containing `wglink/` for outgoing bundles and `wgreturn/` for returns. Its
   `runs/` child is the **Runs folder**, containing run archives and captured CAD
   documents. Earlier folder choices are kept as read-only overrides; choosing a
   root moves no files. WG and Fusion share the mirrored WGLink folder setting,
   so you do not set a second path in Fusion.
5. Keep WG running while using **Send to WG** or **Solve in WG**. These commands
   hand a request to WG; they do not launch WG for you.

If WG says **“WGLink activation is pending until Fusion closes”**, close Fusion
and leave WG running, or restart WG with Fusion closed. Then reopen Fusion.
After a healthy startup, packaged WG installs or updates WGLink for CAD Link
users to the exact add-in revision bundled with that WG release, without a
network connection.
For those users it replaces a hand-copied add-in that no WG installation manages.
Without a CAD Link signal it leaves Fusion's AddIns folder untouched. A developer
copy, a symlink or one owned by another WG installation is preserved; resolve
that installation choice if WG reports an incompatible copy. A displaced
WG-managed copy is kept in WG's application data folder for rollback; an
unmanaged copy is not kept.

WG also checks Fusion's registration list and reports duplicate registrations,
a copy registered from another folder, or a missing **Run on Startup** setting.
It does not edit Fusion's list. Keep one registration pointing at the intended
copy and tick the checkbox in Fusion.

**[Screenshot placeholder: WG's CAD Link settings and Fusion's Run on Startup checkbox.]**

## 2. Send the waveguide to Fusion

1. Finish the basic waveguide in WG. From the design menu, choose
   **Export → Send to CAD** with Fusion selected as the CAD application.
   **Open in Fusion 360** on the linked-design card is another entry point.
   You can also use **Send to CAD** in a completed parametric run's export menu
   to send that run's saved design. WG writes the bundle and starts or raises
   Fusion. The CAD Link connection indicator shows when the add-in's heartbeat
   is online: WG has heard from the running add-in.
2. In Fusion, use WGLink's **Insert** to choose the exported `.wglink` bundle
   if it has not been inserted. Automatic insertion and continuous updates are
   off by default; opening Fusion is not proof that the model was inserted.
3. Save and name the Fusion document. That name identifies its CAD runs in WG.
4. Add your flange, baffle, enclosure, woofer openings and multiple-entry horn
   passages using Fusion's normal modelling tools. Keep the physical surfaces
   that the sound encounters, including the added baffle and walls, in the
   exported assembly.

WGLink inserts native Fusion features, with managed throat/mouth interface
sketches and datums: reference planes and axes that locate the horn's openings.
Build added features from these references where possible. Move or joint the
whole WGLink wrapper component to place the horn; moving its managed body away
from those references can make **Update** refuse it.
If you later change the waveguide in WG, send it again and explicitly use
**Update** in Fusion. Inspect your own downstream features after rebuilding.

Imported CAD is a snapshot. Editing it in Fusion requires another Send or Solve
to bring those edits into WG; the apps do not continuously synchronize geometry.

## 3. Decide which bodies to send

1. Leave **Assembly scope** empty in Send/Solve for the root assembly, or select
   one occurrence: a placed copy of a component in Fusion's assembly tree.
   Its subtree means that copy and all components inside it. Check the dialog's
   **Pre-flight** list:
   included bodies, WG links and sources must describe the speaker you intend.
2. Use solid bodies for ordinary walls and baffles. A visible surface body needs
   **Declare Body… → Exterior shell — solve this body's outer surface** if it
   is an acoustic surface. Use **Exclude — leave this body out of the return**
   for modelling aids, then hide those excluded bodies or their occurrences.
   Fusion can otherwise include them in a component export, so a visible
   excluded body is refused. **Declare Body…** is in the WGLink management dropdown.
3. Do not include construction air solids as ordinary speaker bodies. An air
   volume is not a rigid wall. Mesh bodies are not an acoustic replacement for
   the solid and surface geometry this export expects.
4. Check that hiding or excluding a body has not removed part of a source.
   Component exports are checked against what Fusion actually puts in STEP;
   WGLink refuses a scope that cannot safely leave its unwanted bodies out.

Closed solids are the simplest starting point. Open shells need particular care:
BEMPP refuses free edges away from mirror planes. A fully enclosed void inside a
solid can also be refused during STEP healing; re-export it with the cavity
opened to the outside or as separate boundary shells, as the error advises.

### FEM regions

FEM regions solve the bounded air inside chambers and ports, with interfaces
coupled to the exterior BEM field. They are available by default in CAD Link.
In Fusion, create one closed solid air body per region and use WGLink's
**Declare FEM Region…** command. Set the region mesh size and group its faces
as moving diaphragms, exterior interfaces, plane-wave terminations or rigid
walls. Ungrouped faces are rigid. Hide each declared air body before Send/Solve
so it is exported separately from the speaker assembly. Check Pre-flight.
Rigid FEM face groups can carry Miki absorbing lining: specify its positive
thickness in mm and airflow resistivity in Pa·s/m² in WGLink. Moving groups can
set relative motion from -120 to 0 dB (0 dB means full motion). WG lists these
settings beside each prepared group and retains them in the ingest evidence.
The coupled route also passes positive patch weights of painted sources to the
engine; today only native-source records carry patch weights, and those do not
declare FEM regions, so CAD Link returns use the relative level above. An
enabled lumped rear chamber requires declared driver Sd and uses it alone for
stiffness. Motion weights enter the geometric Sd cross-check and fallback. A
channel with any non-unit motion weight keeps its raw generalized load and
omits normalized acoustic impedance; see the driver Sd rules below.

WG lists the prepared regions and shows retained interior pressure on a slice
plane after solving; select a frequency and drive channel in the field controls.
Exterior normal velocity uses a centred pressure difference with step h bounded
by wavelength/50 and 5% of distance to the skin, to limit wave and near-source
curvature error. Float32 pressure noise of about 1e-5 requires kh ≥ 1e-3
(k = 2πf/c), giving roughly 1% precision and a minimum clearance of
0.0032 wavelengths: about 55 mm at 20 Hz and 55 µm at 20 kHz for c = 343 m/s.
The coordinate precision floor (32 float32 coordinate ULPs) may require more
clearance. There is no fixed millimetre floor. Unreliable samples are masked
with the legend reason “Too close to a surface for a reliable velocity”.

FEM has been validated against closed-form references (sealed box, open box,
ported box, plane-wave and closed tubes) on official BEAT CPU float64.
Declared FEM regions use official BEAT CPU float64 or Metal/CUDA/ROCm float32.
Auto and interior-only systems use CPU float64. Ideal and electrodynamic drives
support x0, y0 and x0+y0 symmetry in free space. A reduced driver must move
within every plane that cuts its moving surface. Ground planes and passive
cardioid are unsupported. Several moving groups in one region
can share a drive channel, for example a dome and surround. To share one
diaphragm across front and rear FEM chambers, give that channel an
electrodynamic driver in WG. The driver owns every moving FEM and painted BEM
boundary on its channel and uses one rigid translation coordinate. This also
supports a meshed rear chamber with a painted exterior front, with or without
an exterior port. An ideal-source channel spanning FEM regions is refused:
“give this channel an electrodynamic driver to share a diaphragm between regions”.

Two air regions may touch only on faces both declare moving on that same
channel. Each diaphragm side has an independent mesh; no matching nodes are
required between chambers. Other touching faces remain refused. Enter Sd in
the driver options for a lumped rear chamber; declared Sd controls its stiffness.
Moving geometry supplies only an acoustic-impedance fallback and cross-check.
Opposite-side areas differing by more than 10 % are ambiguous, so enter Sd to
get acoustic impedance. Disable the lumped rear chamber when the rear already
radiates through the moving geometry.
FEM-only channels can coexist with painted exterior sources. A missing
compatible engine refuses the job; choose exterior-only explicitly if the
interior should be excluded.

Plane-wave terminations are supported only in interior-only solves. A return
with a termination and any painted exterior source or interface in any region
produces a non-blocking ingest warning naming the termination's region and group.
Coupled job admission and the builders refuse it, including when setup drive
channels introduce an exterior; exterior-only leaves the regions out and remains
available. Remove the termination group (make it rigid) to solve the chamber
coupled, or remove the painted sources and interfaces to solve the region
interior-only, or choose exterior-only.

Operators can disable FEM regions with `WG2_FEM_REGIONS=0` and restart WG;
`false`, `no` and `off` also disable it. This removes the capability and FEM UI
and refuses FEM intake and coupled jobs. Unset the override to enable it again.

## 4. Mark the faces that produce sound

1. Select the throat or diaphragm face that drives the air, rather than a magnet,
   basket or mounting flange. A WGLink-inserted waveguide already has its managed
   throat source; check that it survives your edits.
2. For added drivers, choose **Set WG Source…**, select the face or faces, and
   choose **LF**, **MF** or **HF**. For example, use HF for the compression-driver
   throat, MF for a group of midrange diaphragms feeding the horn, and LF for
   woofers on the enclosure baffle.
3. Save the Fusion document after marking. Check the role, face count and area
   in Send/Solve's **Pre-flight** summary before exporting.
4. To remove an accidental marking, select the faces and use
   **Set WG Source… → Clear WG source**. After splitting, copying, deleting or
   repainting a source face, select all its intended faces and mark them again
   if WGLink asks. Recheck the driver settings in WG after reassignment.

The command applies an appearance named exactly after the role and stamps the
faces with a persistent source identity, so later sends refer to the same source.
Body and component names alone do not
mark sources. Hand-painted appearances with the recognized role names are also
read, but old paint may need adoption or explicit reassignment before export.
An appearance called `HF drive` or `MF_L` does not create a source in this add-in.

Extra, painted faces of the same role form **one source**, even when they are
disconnected. Mark all four identical midrange diaphragms MF to drive them as a
group. A managed WGLink throat has its own identity per inserted instance.
Extra painted HF faces are separate from that managed throat source. The add-in
has no control for inventing independent `MF1`, `MF2`, etc. sources or giving
same-role painted drivers separate gains and delays. Use the supported group
only when those drivers are meant to share the drive.

**Side names:** imported source names, IDs and selector labels such as `MFLeft`,
`HF_R`, `woofer_L1` or `PORT_EXIT_L` identify a particular side to WG's mirror
safeguard. WG refuses to mirror a model already cut in CAD if a source belongs
to the left or right side: a mirror would impersonate the other driver. Send
the whole model when the sides need independent sources. These names are
recognized by the safeguard, not offered as source roles by **Set WG Source…**;
renaming a Fusion body does not create separate left/right channels.

**[Screenshot placeholder: selected diaphragm faces in Set WG Source, followed by the Send pre-flight source list.]**

## 5. Set up the drivers in WG

1. Use **Send to WG** first when you want to inspect the model and set its drive
   before solving. WG imports and prepares the snapshot and shows it in CAD mode.
   Open the **Simulation** rail's **Drivers** section.
2. Check each channel and its source IDs. A channel is a group driven together;
   one source can contain several disconnected diaphragm patches.
3. Keep **Motion → Normal** for driver-model solves. It moves each face along
   its own normal. **Axial (pistonic)** describes a rigid piston along its source
   axis. Metal and BEMPP solve it along each source's own axis with the module
   versions WG ships; an older installed Metal or BEMPP package that lacks
   per-source axes refuses it. WG reports any refusal before running; it does
   not silently use Normal. An axial channel cannot carry a Thiele/Small driver
   model either. BEAT can use CAD axial motion only when each resolved source
   axis lies along the radiation axis, in either direction. Tilted axes are
   refused. A source with no usable outward axis, or motion incompatible with
   a mirror plane, is also refused on every engine; send the whole model when
   the mirror is the problem.
4. Choose a driver from the library or enter its data by hand. Choose the correct
   impedance variant and check the datasheet values. A started driver needs
   **Sd, Bl, Re**, one of **Mmd/Mms**, and one of **Cms/Vas/Fs**. If you enter
   **Le2** or **Re2**, enter both. The app shows missing values and refuses an
   incomplete driver; clear the driver to return to a unit-drive solve.
5. Set **Count** to the number of identical drivers sharing the source,
   **wired in parallel** and moving together. The driver model uses the parallel
   resistance, Re divided by Count. Series or series-parallel wiring gives
   incorrect electrical results and voltage sensitivity with this model.
   Set **Rear volume, total for all drivers** to their one shared sealed chamber
   volume, in litres. Enter the per-driver T/S data; do not multiply Sd or the
   chamber volume by Count. Keep the full speaker's Count for a mirrored half
   or quarter solve. CAD geometry does not fill in these driver numbers.
6. Set **Drive voltage** in RMS volts. Review **Crossover** when more than one
   channel is present: it controls their combined filters, levels, polarity and
   delays. Check the defaults before trusting a combined response.

A driver model requires Normal motion and exactly one source on its channel.
That source may be the disconnected group of identical drivers above. A channel
containing several separate source identities cannot carry one driver model.
Without a driver model, the channel gives a reference response to **unit
acceleration**: WG drives the source with an acceleration of 1 m/s² at each
frequency, rather than predicting its motion from a real driver's voltage.
This is useful for studying response and directivity, but it does not provide
the driver's voltage sensitivity, electrical impedance, power or excursion.

When **Solve driver in BEAT** is enabled, driver options include **Sd (cm²)**.
Choosing a library driver replaces its Sd and available bare-driver parameters;
fields missing from the new library entry are cleared. Check the datasheet and
enter missing values, especially bare Mmd (Mms includes air load and is not
substituted). Sd is stored in m² in a v2 transducer spec. Every enabled lumped
sealed rear chamber requires an entered Sd, at admission and compilation,
on both exterior-only and coupled FEM routes. The declared value controls
chamber stiffness ρc²Sd²/V and, for unit motion weights, acoustic impedance Z/(ρcSd).
Declared Sd is the full physical driver area, including on half and quarter
models; WG never scales it. Geometry on a reduced mesh is completed to the
full driver area before the fallback or cross-check. Mirrored complete drivers
share the channel voltage; their currents and powers add while excursion
remains per driver. Exterior-only drivers must move within every active
symmetry plane, including complete mirrored drivers.

Motion weights enter the geometric cross-check and fallback as per-face weights.
If any moving boundary of a channel has a weight other than 1, results retain
its raw generalized load and omit normalized acoustic impedance with
“acoustic impedance is not normalised for weighted motion”.

Moving-face geometry supplies acoustic-impedance normalization when Sd is
undeclared and geometry is unambiguous. Same-sign mesh net projections are
summed as one side; opposite sides are averaged only for this normalization.
More than 10% disagreement makes geometry ambiguous and produces a warning.
Geometry is also a cross-check when unambiguous: it includes
everything that moves, including the surround roll, so 10–20% above datasheet
Sd is normal. Results warn on a geometric cross-check mismatch and record
the value and its source per channel. Ambiguous geometry without Sd omits
acoustic impedance with “Sd not determined; enter Sd to get acoustic impedance”.
An enabled lumped rear chamber refuses `lumped_chamber_double_rear_load` when
moving mesh nets or disconnected moving patches have opposite signs, or when
geometry is ambiguous. Same-sign meshes may contribute to one side. The
meshed rear load and lumped rear stiffness must never be applied together.

## 6. Ports and rear chambers

1. Model the actual passages and openings in Fusion. A hole alone does not tell
   WG what motor, chamber or port drives it. An ordinary bass-reflex enclosure
   is not automatically recognized and coupled just because you drew its port.
2. For the supported passive-cardioid case, mark the diaphragm **MF** and the
   port aperture **PASSIVE_CARDIOID**. The old appearance name **PORT_EXIT** is
   still recognized. This is a passive-cardioid port role, not a general-purpose
   driver or an automatic bass-reflex setup.
3. In **Simulation → Passive cardioid**, enable the model and enter rear volume,
   port length, physical port area and foam resistance. Physical port area and
   the meshed aperture area are different inputs; choose **Same as the BEM
   aperture** only when they should be equal.
4. Choose **Metal** for this feature. **Coupled** needs exactly one MF diaphragm
   source alone on a complete driver channel and all port patches on one other
   channel. Use the cardioid rear-volume field for this coupled chamber; leave
   that MF driver's separate rear-volume field empty to avoid counting it twice.
   A crossover containing the raw MF channel is also refused. Its driver-based
   scaling belongs to the derived `passive_cardioid` channel; use that channel
   in the combined response instead of the raw MF reference response.

Passive cardioid is Metal-only. Declared FEM regions use official BEAT CPU
float64 or Metal/CUDA/ROCm float32; Auto uses CPU float64. Electrodynamic driver
models support full, half and quarter models. FEM regions
cannot be combined with passive cardioid in this release.
**Exterior-only (leave FEM regions out)** excludes the interior air. Legacy declared air volumes also require this explicit exclusion.
The sealed rear-volume driver model and passive-cardioid model above add chamber
loads without a FEM region. Structural vibration and cone breakup are not modelled.

## 7. Check the axis and symmetry

1. For a model drawn from scratch, check **Radiates along** on the Solve card.
   WG uses a confident automatic axis and records it in the run. If it cannot
   decide, it asks **“Which way does the mouth face? (CAD axes)”**. Choose +X,
   −X, +Y, −Y, +Z or −Z and inspect the side/top previews. The outgoing direction
   becomes solver +Z, shown in blue; sources are orange. **Solve** confirms the
   shown choice for the project. Use **Change** if you rotate the model later.
   Exporting in a different component's coordinates can require confirmation
   again. An unsaved Fusion document remembers confirmation only for that
   snapshot; save the document for continuity between returns.
2. Prefer sending the whole speaker. WG checks the finished geometry and each
   source identity for mirror symmetry. One accepted transverse mirror gives a
   half solve; two give a quarter; otherwise WG keeps the full domain. Keep
   flanges, enclosure, ports, driver patches and their drive symmetric across
   every mirror you intend to use. Matching outer dimensions alone is not enough.
3. For a model drawn in CAD, place the throat at the origin and centre the
   speaker on the two planes through its radiation axis if you want WG to
   detect mirror symmetry. Read the domain line on the Solve card. For a whole
   model, **Mesh detail → Force full domain**, then **Rebuild mesh**, disables WG's automatic cutting
   when you want to check a doubtful symmetry result.
4. If you cut the model in Fusion, use the YZ or XZ origin plane (x = 0 or y = 0)
   with radiation along model +Z. Leave the acoustic cut boundary open; a flat
   cap there would be solved as a wall. Sources must still be identified, with
   a source meeting each cut plane, no other leaking edges and no side-specific
   source identity. WG can recover a clean negative-side half by reflection and
   states that on the card. Positive-side cuts are the simplest convention.
5. Use the domain line's **Change** only for the alternative readings WG offers.
   Solve prepares it again under that choice. An unsafe open half is refused;
   **Force full domain** cannot restore geometry missing from the file.

Front/back cuts, off-origin or oblique cuts, and a CAD-cut model facing an axis
other than model +Z are not supported for mirror recovery. Send the complete
model instead. Full/half/quarter describe the acoustic domain, not how much of
the model you happen to display in the viewport.

Electrodynamic drivers support transverse half and quarter domains. A channel
represents one driver when its moving patches connect through shared edges after
coordinate welding, or their footprints projected along the motion axis overlap
by at least 50% of the smaller patch's projected area. Zero-projection faces still
carry connectivity. Shared edges join separately tagged dome, surround and cap
surfaces; overlapping front and rear skins may share a driver in one mesh or
across FEM chambers and the exterior mesh. Separate diaphragms, including a coax
or an off-plane mirrored pair, need separate channels even across different meshes.
Otherwise WG refuses `driver_disconnected_patches`: give each driver its own drive
channel, or, if these are the two faces of one diaphragm, make them overlap along
the motion axis / paint the rim as moving.

Full and reduced models use the same rule. Under symmetry it judges the
representative; orbit images are separate physical drivers by construction,
each with its own mass and electrical parameters and parallel current contribution.
For a full-domain comparison, give each mirrored driver its own channel and
combine their voltage bases.

A moving perimeter edge on a mirror plane is treated as a driver cut, including
an edge that only touches the plane. The driver's motion axis must lie in each
cutting plane. If it does not, prepare complete geometry with **Force full domain**.

**[Screenshot placeholder: Solve card showing Radiates along, the domain line and Change, with a quarter-model preview.]**

## 8. Send, choose a solver, and solve

1. In Fusion, choose **Send to WG** to import/display without solving, or
   **Solve in WG** to export and request a solve of that exact snapshot. Review
   **Assembly scope** and sources. If several linked placements are included,
   **Solver anchor instance** chooses the placement whose throat position and
   direction define the solve's origin and radiation axis. Choose the horn
   whose direction you want the plots to describe. Before requesting a return,
   choose its matching instance ID in WG's **Managed Fusion link**; freshness
   checks, geometry requests and updates use that exact placement. Back in
   Fusion, choose the same instance as the solver anchor, then press OK.
   WG does not guess which placement you mean. Preparation refuses
   a repeated design with no selected instance, or a selected instance that
   conflicts with the return's solver anchor.
2. In WG, review **Frequency Sweep**, **Directivity Map**, **Drivers**,
   **Crossover** and **Mesh detail**. Smaller mesh sizes are finer. Use
   **Rebuild mesh** after changing mesh inputs. Review any findings before using
   **Approve and solve**; approving a warning is not a geometry repair.
3. Choose the solver in **Simulation → Solve options**. **AUTO** chooses among
   compatible available engines. An explicit choice stays yours; WG refuses an
   unsupported combination rather than silently switching engines.

   | Solver | CAD use today |
   |---|---|
   | **Metal** | Apple GPU; Normal and axial CAD sources, and passive cardioid. An older installed package without per-source axes refuses CAD axial. |
   | **BEMPP** | Imported CAD requires OpenCL assembly and no free rim away from mirror planes. CAD axial is solved along each source's axis; an older installed package without per-source axes refuses it. |
   | **BEAT · CPU** | Requires its provisioned Julia runtime. Can solve imported CAD; passive cardioid is refused. |
   | **BEAT · Metal / CUDA / ROCm** | Requires the matching available GPU/runtime. CUDA and ROCm, on the official default route, are **not hardware-qualified**. The official engine offers them on Linux and Windows. Choose Accurate for CUDA/ROCm CAD solves; explicit BEAT · Metal also accepts CAD in Fast. Passive cardioid is refused. |

   AUTO in Fast skips BEAT GPU engines for CAD. Read the solver row's reason
   when it is unavailable: the actual returned domain, source motion and runtime
   all affect eligibility. Switching from Metal to another engine does not
   remove these checks.
4. Press **Solve** in WG after correcting a waiting request's settings. The
   settings and retained model are kept for retry; ordinary settings corrections
   do not require re-exporting from Fusion. First-time Fusion solves without a
   saved setup use WG's shared defaults and say **“Using WG's default settings —
   change them in WG.”** Review them before making design decisions.

You can also start a return from WG. **Refresh** (the circular-arrow button
with the tooltip **Refresh the CAD return listing**) lists bundles already in
the exchange folder; it does not export fresh geometry from Fusion. When the
link card offers **Bring in & solve**, it asks Fusion for its current model,
prepares it and starts the solve. The command palette's **Pull from Fusion &
Solve** does the same. These requests need WGLink running in Fusion.

If a request needs your input, the Solve card shows **Fusion asked for a solve**
and the reason it is waiting. Correct the settings or review the findings, then
use **Solve** or **Approve and solve** as offered. **Dismiss** retires the
request. Completing or dismissing it consumes it, so it cannot start another
solve later. A temporarily unavailable runtime can leave a request waiting;
a model or chosen engine that cannot support the solve is refused with a reason.

Imported CAD solves are free-space solves. Infinite-baffle mounting and a rigid
ground plane are not supported on this route. The imported diagonal observation
plane supports only the default 45° inclination.

## 9. Read and keep the results

1. Follow progress in WG and open the completed run. The run belongs to the
   Fusion document and snapshot that was submitted, even if you edit CAD while
   it runs. Check **Solve inputs**, the recorded axis and domain before comparing
   designs. Completed runs retain their original settings.
2. Inspect each channel's response and then the combined output, if enabled.
   Use the horizontal/vertical directivity maps and polar plots to judge coverage;
   a normalized map describes pattern, not absolute sensitivity. Driver-based
   runs also provide impedance, electrical power/current and excursion views.
3. Read warnings and failed-frequency information. A plot with missing samples
   is not evidence of a clean full sweep. Compare mesh refinements before relying
   on a fine ripple or a high-frequency feature.
4. Export the run's report, FRD/polar files for VituixCAD, CSVs or retained solve
   mesh from the run export menu. Open the project folder for its saved files.
   **Settings → CAD Link → Keep a copy of the Fusion model** controls document
   retention; do not assume a reopenable Fusion document was captured when that
   option is disabled.

Returned snapshots appear under **Model versions** in CAD Link; completed
solves appear under **Runs**. On WG startup, an existing return is selected
without preparation; choose **Prepare simulation**. Newly arriving returns and
ones you select from **Model versions** are prepared automatically. If a
preparation fails, use **Prepare simulation** to retry, or **Prepare again**
when that snapshot already has a preparation record.

For returns from the same project with compatible sources (the same source
IDs, roles and required flags), WG keeps mesh sizing, channel mapping, drivers,
combination settings and the frequency sweep. Check them after changing source
markings; an incompatible source inventory needs its own setup. Retrying the
same preparation keeps the findings you already approved for it. A different
preparation — for example, a changed snapshot, settings or frame — needs fresh
review, even if a warning sounds the same.

The exchange folder is for transfers. The **Workspace** archive keeps runs by
design, with retained Fusion documents under `runs/<design>/cad/` when capture
is enabled and each run in its own folder alongside them. Parametric and CAD
runs for the same design share that folder. See the [run archive](USER-GUIDE.md#the-run-archive)
for retention and export details.

## 10. Common refusals and remedies

These are exact messages or excerpts. WG shows the messages unless a row says
**Fusion shows**; those rows come from the bundled WGLink add-in, not WG.
Variable source names and measurements are omitted from excerpts. The cut-plane
examples show x = 0; the same checks apply at y = 0.

| Message | What to do |
|---|---|
| **Fusion shows:** “Return export has no drivable source. Paint an included face LF, MF, HF, or PASSIVE_CARDIOID and try again.” | Include the driver face and use **Set WG Source…**. Check Pre-flight again. |
| **Fusion shows:** “source was split or copied:” | Select all intended faces of that source and run **Set WG Source…** again. Check its driver setup after sending. |
| **Fusion shows:** “this export would carry only part of that source” | Include its hidden/excluded members, or clear their source markings if they should no longer belong. |
| **Fusion shows:** “is declared 'exclude' but is still visible” | Hide the excluded body or its occurrence in Fusion, or clear the declaration to include it. |
| “a face lies in x = 0 and would solve as a wall across the cut” | Remove the acoustic cap at that cut, or send the whole model. The same rule applies at y = 0. |
| “no source meets x = 0, so nothing shows it is the speaker's symmetry plane rather than an open side” | Send the whole model or correct the cut and source geometry. Do not invent a source just to pass the check. |
| “its mirror image would stand in for the other side's own source” | Send the whole model for independently identified sides. Rename only if the name wrongly identifies a shared source as one side's driver. |
| “Return the whole model from CAD to solve it whole.” | Turn **Force full domain** off for a valid CAD-cut model, or send the complete speaker. |
| “Installed hornlab-metal-bem does not support per-source axial axes.” / “Installed hornlab-bempp-bem does not support per-source axial axes.” | The installed Metal or BEMPP package is older than the version WG ships and cannot solve that CAD axial drive. Update the module, choose Normal only if it represents the intended motion, or use an available compatible BEAT route without a driver model. |
| “BEAT drives axial sources along its z axis” | That source's axis is tilted relative to the radiation axis. Use Normal only if appropriate; BEAT cannot represent that tilted axial drive. |
| “Complete the driver, or clear it to solve that channel unit-driven.” | Supply the listed missing T/S values, or clear the driver. |
| “BEMPP does not solve the passive cardioid; it is solved on Metal only. Select Metal for this return.” | Choose Metal. A CPU/GPU backend change cannot preserve this feature on BEMPP or BEAT. |
| “BEMPP solves imported CAD geometry on an OpenCL device only.” | Use an available compatible engine, or an OpenCL assembly setup the app accepts. A free-rim refusal additionally requires closing the shell or choosing Metal/BEAT · CPU. |
| “imported geometry supports free-space solves only; infinite baffle is unavailable” | Use free-space mounting for this CAD solve. |
| “Turn the ground plane off to solve this return.” | Disable the ground plane; imported CAD cannot be stood above a floor on this route. |
| **Fusion shows:** “WG took the request but has not confirmed it. Check WG's CAD Link panel before sending this model again.” | Inspect CAD Link first. Sending again creates a new request and may duplicate work. |

If Fusion reports that WG has not picked up the request for a minute, open WG,
check that it is current and collecting requests, and let it handle the waiting
request. If geometry must change, correct it in Fusion and send again. For an
export error's full traceback, use **View → Show Text Commands** in Fusion.

## 11. STEP from another CAD program

There is **no standalone STEP import-and-solve command in WG's current UI**.
**Open…** reads WG/ATH design files. **Import mesh…** independently imports
ASCII Gmsh 2.2 triangular surface meshes for inspection and full-domain solving;
choose units and driven physical groups in its setup panel. See the
[mesh workflow](USER-GUIDE.md#import-and-solve-a-mesh) for its first-slice limits.
This is not standalone STEP import. The CAD solve route still accepts a
validated return bundle containing geometry and source information.

1. Export STEP from your other CAD program and open it in Fusion.
2. Inspect the imported bodies, mark the source faces with WGLink, and save the
   Fusion document.
3. Follow Send/Solve above as a model authored in CAD. A plain STEP imported this
   way has no WG-managed parametric link.

A completely non-Fusion, plain-STEP user workflow is not supported today.
Onshape is disabled in release builds, so it is not an end-user substitute for
that missing import route.

### Onshape in development builds

Onshape is unavailable in release builds. In a development run explicitly
enabled with `WG2_ENABLE_ONSHAPE=1`, choose Onshape in **Settings → CAD Link**:

1. Create a personal key pair in Onshape under **My account → Developer → API keys**.
2. Save `ONSHAPE_ACCESS_KEY` and `ONSHAPE_SECRET_KEY` in the private credential
   file shown in WG's settings. Keep it outside the repository and shared
   folders, and restrict access to your own account. WG reads it locally;
   it does not ask for the secret in a browser field or return it through its API.
3. Use **Check connection** and check the account and plan before sending.
   A free-plan document is public; WG asks for explicit consent before creating it.

Onshape sends and returns are in the CAD Link panel. It builds an internal
`.wglink` bundle and uploads directly, using its own transport rather than the
Fusion exchange folder. The design and parametric-run export menus' **Send to
CAD** is the Fusion folder transport and appears when Fusion is selected.
