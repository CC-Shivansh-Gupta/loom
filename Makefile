# ROS Humble on this machine registers pytest plugins that crash the
# user-site pytest, so autoload is disabled for this project.
test:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest

demo:
	python3 -m loom.run configs/plant_demo.yaml --hours 2
	@echo
	python3 -m loom.run configs/ramp_b3.yaml --hours 2

views:
	python3 -m loom.run configs/ramp_b3.yaml --hours 0.75 --view operator:B3 --view supervisor --view manager

.PHONY: test demo views
