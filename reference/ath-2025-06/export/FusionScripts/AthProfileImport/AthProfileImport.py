
import adsk.core, adsk.fusion, traceback

def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        design = app.activeProduct
        if not design:
            ui.messageBox('No active Fusion design', 'Ath Profile Import')
            return
        root = design.rootComponent
        
        dlg = ui.createFileDialog()
        dlg.title = 'Open Ath Profile'
        dlg.filter = 'Ath Profile Definition (*.afp);;All Files (*.*)'
        if dlg.showOpen() != adsk.core.DialogResults.DialogOK:
            return

        f = open(dlg.filename, 'r')
        sketch = root.sketches.add(root.xYConstructionPlane)
        lines = sketch.sketchCurves.sketchLines
        points = {}
        
        line = f.readline().rstrip()
        while line:
            if len(line) < 3 or line[0] == '#':
                line = f.readline().rstrip()
                continue
            items = line.split(' ')
            if line[0] == 'P' and len(items) >= 4:
                points[items[1]] = adsk.core.Point3D.create(
                    0.1*float(items[2]), 0.1*float(items[3]), 0.0
                )
            elif line[0] == 'L' and len(items) >= 3:
                lines.addByTwoPoints(points[items[1]], points[items[2]])
            
            elif line[0] == 'S' and len(items) >= 3:
                splinePoints = adsk.core.ObjectCollection.create()
                for k in range(int(items[1]), int(items[2]) + 1):
                    splinePoints.add(points[str(k)])
                sketch.sketchCurves.sketchFittedSplines.add(splinePoints)
            
            elif line[0] == 'U':
                splinePoints = adsk.core.ObjectCollection.create()
                for k in items[1:]:
                    splinePoints.add(points[k])
                sketch.sketchCurves.sketchFittedSplines.add(splinePoints)
                
            line = f.readline().rstrip()
        f.close()
            
    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

