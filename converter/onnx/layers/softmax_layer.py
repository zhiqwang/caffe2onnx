import logging
from onnx import helper
from onnx import TensorProto as tp


from layers.base_layer import BaseLayer


class SoftmaxLayer(BaseLayer):
    def __init__(self, layer, name=None):
        super(SoftmaxLayer, self).__init__(layer, name)

    def generate_node(self):
        node = helper.make_node(
            "Softmax", self._in_names, self._out_names, self._layer.name, axis=-1
        )
        logging.info("softmax_layer: " + self._layer.name + " created")
        self._node = node
