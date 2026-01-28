
import adsk.core, adsk.fusion, traceback

def run(context):
	ui = None
	try:
		title = 'Ath4 Surface Import'
		app = adsk.core.Application.get()
		ui = app.userInterface
		design = app.activeProduct
		if not design:
			ui.messageBox('No active Fusion design', title)
			return
		root = design.rootComponent
		
		dlg = ui.createFileDialog()
		dlg.title = 'Open Ath4 Coordinate File'
		dlg.filter = 'Comma Separated Values (*.csv);;All Files (*.*)'
		if dlg.showOpen() != adsk.core.DialogResults.DialogOK:
			return

		filename = dlg.filename
		f = open(filename, 'r')
		points = adsk.core.ObjectCollection.create()		
		points_array = []
		line = f.readline()
		while line:			   
			if line[0] == '#':
				line = f.readline()
				continue
			if line in ['\n', '\r\n']:
				if len(points) > 0:
					points_array.append(points)
					points = adsk.core.ObjectCollection.create()
			else:
				pntStrArr = line.split(';')
				if len(pntStrArr) >= 3:
					points.add(adsk.core.Point3D.create(
							float(pntStrArr[0]), float(pntStrArr[1]), float(pntStrArr[2])
						)
					)					
			line = f.readline()
		f.close()
		if len(points) > 0:
			points_array.append(points)
		
		loftFeats = root.features.loftFeatures
		loftInput = loftFeats.createInput(adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
		loftSectionsObj = loftInput.loftSections

		for pa in points_array:
			sketch = root.sketches.add(root.xYConstructionPlane)
			sketch.sketchCurves.sketchFittedSplines.add(pa)
			loftSectionsObj.add(sketch.profiles.item(0))

		loftInput.isSolid = False	
		loftFeats.add(loftInput)
			
	except:
		if ui:
			ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

