import logging
from onnx import helper
from onnx import TensorProto as tp


from layers.base_layer import BaseLayer


class BnllLayer(BaseLayer):
    def __init__(self, layer):
        super(BnllLayer, self).__init__(layer)

    def generate_node(self):
        node = helper.make_node(
            "Softplus", self._in_names, self._out_names, self._layer.name
        )
        logging.info("Bnll_layer: " + self._layer.name + " created")
        self._node = node
