import os
import sys
import logging

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import caffe
from caffe.proto import caffe_pb2
from google.protobuf import text_format

import numpy as np

from onnx import save, helper, checker

import onnxruntime as rt

import layers as ops

logging.basicConfig(
    format="%(asctime)s %(levelname)-5s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


class caffe2onnx_converter:
    def __init__(self, proto_file, weight_file, onnx_file_name):
        self.proto_file = proto_file
        self.weight_file = weight_file
        self.onnx_file_name = onnx_file_name
        self.save_path = os.path.dirname(os.path.abspath(self.proto_file))

    def run(self):
        self.model_def = self._load_caffe_prototxt()
        self.model_weights = self._load_caffe_weight()
        self.caffe_net = self._load_net()

        self.in_tensor_value_info = []
        self.nodes = []  # nodes in graph
        self.out_tensor_value_info = []
        self.init_tensor = []
        self.inplace_dict = {}

        for layer in self.model_def.layer:
            if layer.type == "Input":
                pass
            elif layer.type == "Convolution":
                conv_layer = ops.ConvLayer(layer)
                for idx in range(len(layer.bottom)):
                    if layer.bottom[idx] in self.inplace_dict.keys():
                        last_key = list(self.inplace_dict[layer.bottom[idx]].keys())[-1]
                        last_layer_output_name = self.inplace_dict[layer.bottom[idx]][
                            last_key
                        ]["new_output"]
                        conv_layer._in_names.append(last_layer_output_name)
                    else:
                        conv_layer._in_names.extend(list(layer.bottom))

                conv_layer._out_names.extend(list(layer.top))

                params = self.caffe_net.params[layer.name]
                params_numpy = self._param_to_numpy(params)

                conv_layer.generate_params(params_numpy)
                conv_layer.generate_node()

                self.nodes.append(conv_layer._node)

                self.in_tensor_value_info.extend(conv_layer._in_tensor_value_info)
                self.init_tensor.extend(conv_layer._init_tensor)
            elif layer.type == "BatchNorm":
                batchnorm_layer = ops.BatchNormLayer(layer)

                batchnorm_layer._in_names.extend(list(layer.bottom))

                if batchnorm_layer._is_inplace == True:
                    this_layer_output_name = layer.name + "_output"
                    self._update_inplace_dict(
                        layer, layer.top[0], this_layer_output_name
                    )
                    batchnorm_layer._out_names.append(this_layer_output_name)
                else:
                    batchnorm_layer._out_names.extend(list(layer.top))

                params_batchnorm = self.caffe_net.params[layer.name]
                params_batchnorm_numpy = self._param_to_numpy(params_batchnorm)

                idx = self._get_layer_index(layer)
                if (
                    idx < self._get_net_length() - 1
                    and self.caffe_net.layers[idx + 1].type == "Scale"
                ):
                    params_scale = self.caffe_net.params[
                        self.caffe_net._layer_names[idx + 1]
                    ]
                    params_scale_numpy = self._param_to_numpy(params_scale)
                    batchnorm_layer.generate_params(
                        params_batchnorm_numpy, params_scale_numpy
                    )
                else:
                    default_param_scale = [
                        np.ones(shape=params_batchnorm_numpy[0].shape, dtype=np.float),
                        np.zeros(shape=params_batchnorm_numpy[1].shape, dtype=np.float),
                    ]
                    batchnorm_layer.generate_params(
                        params_batchnorm_numpy, default_param_scale
                    )

                batchnorm_layer.generate_node()
                self.nodes.append(batchnorm_layer._node)

                self.in_tensor_value_info.extend(batchnorm_layer._in_tensor_value_info)
                self.init_tensor.extend(batchnorm_layer._init_tensor)
            elif layer.type == "Scale":
                idx = self._get_layer_index(layer)
                if self.caffe_net.layers[idx - 1].type == "BatchNorm":
                    continue
            elif layer.type == "ReLU":
                relu_layer = ops.ReluLayer(layer)
                if relu_layer._is_inplace == True:
                    this_layer_output_name = layer.name + "_output"
                    if layer.top[0] in self.inplace_dict.keys():
                        last_key = list(self.inplace_dict[layer.top[0]].keys())[-1]
                        last_layer_output_name = self.inplace_dict[layer.top[0]][
                            last_key
                        ]["new_output"]
                        relu_layer._in_names.append(last_layer_output_name)
                        self._update_inplace_dict(
                            layer, last_layer_output_name, this_layer_output_name
                        )
                    else:
                        relu_layer._in_names.append(layer.top[0])
                        self._update_inplace_dict(
                            layer, layer.top[0], this_layer_output_name
                        )

                    relu_layer._out_names.append(this_layer_output_name)
                    relu_layer.generate_node()
                    self.nodes.append(relu_layer._node)
            elif layer.type == "Pooling":
                pooling_layer = ops.PoolingLayer(layer)
                for idx in range(len(layer.bottom)):
                    if layer.bottom[idx] in self.inplace_dict.keys():
                        last_key = list(self.inplace_dict[layer.bottom[idx]].keys())[-1]
                        last_layer_output_name = self.inplace_dict[layer.bottom[idx]][
                            last_key
                        ]["new_output"]

                pooling_layer._in_names.append(last_layer_output_name)
                pooling_layer._out_names.extend(list(layer.top))

                shape = self.caffe_net.blobs[layer.bottom[0]].data.shape

                pooling_layer.generate_node(shape)
                self.nodes.append(pooling_layer._node)
            elif layer.type == "Eltwise":
                eltwise_layer = ops.EltwiseLayer(layer)

                for idx in range(len(layer.bottom)):
                    if layer.bottom[idx] in self.inplace_dict.keys():
                        last_key = list(self.inplace_dict[layer.bottom[idx]].keys())[-1]
                        last_layer_output_name = self.inplace_dict[layer.bottom[idx]][
                            last_key
                        ]["new_output"]
                        eltwise_layer._in_names.append(last_layer_output_name)
                    else:
                        eltwise_layer._in_names.append(layer.bottom[idx])
                eltwise_layer._out_names.extend(list(layer.top))

                eltwise_layer.generate_node(shape)
                self.nodes.append(eltwise_layer._node)
            elif layer.type == "InnerProduct":
                reshape_layer = ops.Reshapelayer(layer)

                shape = self.caffe_net.blobs[layer.bottom[0]].data.shape
                reshape_out_name = layer.name + "_reshape_out"

                reshape_layer._in_names.extend(list(layer.bottom))
                reshape_layer._out_names.append(reshape_out_name)

                reshape_layer.generate_params(shape)
                reshape_layer.generate_node()
                self.nodes.append(reshape_layer._node)
                self.in_tensor_value_info.extend(reshape_layer._in_tensor_value_info)
                self.init_tensor.extend(reshape_layer._init_tensor)

                gemm_layer = ops.GemmLayer(layer)
                gemm_layer._in_names.append(reshape_out_name)
                gemm_layer._out_names.extend(list(layer.top))
                params = self.caffe_net.params[layer.name]
                params_numpy = self._param_to_numpy(params)

                gemm_layer.generate_params(params_numpy)
                gemm_layer.generate_node()
                self.nodes.append(gemm_layer._node)
                self.in_tensor_value_info.extend(gemm_layer._in_tensor_value_info)
                self.init_tensor.extend(gemm_layer._init_tensor)
            elif layer.type == "Softmax":
                softmax_layer = ops.SoftmaxLayer(layer)
                softmax_layer._in_names.extend(list(layer.bottom))
                softmax_layer._out_names.extend(list(layer.top))
                softmax_layer.generate_node()
                self.nodes.append(softmax_layer._node)
            else:
                raise Exception("unsupported layer type: {}".format(layer.type))

        for input_name in self.caffe_net.inputs:
            shape = self.caffe_net.blobs[input_name].shape
            shape_str = " ".join(str(e) for e in shape)
            logging.info("caffe output: " + input_name + "shape: " + shape_str)

            input_layer = ops.InputLayer()
            input_layer._generate_input(input_name, shape)
            self.in_tensor_value_info.extend(input_layer._in_tensor_value_info)

        for output_name in self.caffe_net.outputs:
            shape = self.caffe_net.blobs[output_name].shape
            shape_str = " ".join(str(e) for e in shape)
            logging.info("caffe output: " + output_name + "shape: " + shape_str)
            if output_name in self.inplace_dict.keys():
                last_key = list(self.inplace_dict[output_name].keys())[-1]
                output_name = self.inplace_dict[output_name][last_key]["new_output"]

            output_layer = ops.OutputLayer()
            output_layer._generate_output(output_name, shape)
            self.out_tensor_value_info.extend(output_layer._out_tensor_value_info)

        graph_def = helper.make_graph(
            self.nodes,
            self.onnx_file_name,
            self.in_tensor_value_info,
            self.out_tensor_value_info,
            self.init_tensor,
        )

        self.model_def = helper.make_model(graph_def, producer_name="caffe")
        self._freeze()
        checker.check_model(self.model_def)
        logging.info("onnx model conversion completed")

    def save(self):
        logging.info(
            "onnx model saved to "
            + self.save_path
            + os.sep
            + self.onnx_file_name
            + ".onnx"
        )
        save(self.model_def, self.save_path + os.sep + self.onnx_file_name + ".onnx")

    def test(self):
        onnx_rt_dict = {}
        for input_name in self.caffe_net.inputs:
            shape = self.caffe_net.blobs[input_name].shape
            input_data = np.random.rand(*shape).astype(np.float32)
            shape_str = " ".join(str(e) for e in shape)
            self.caffe_net.blobs[input_name].data[...] = input_data
            logging.info("caffe input: " + input_name + "shape: " + shape_str)
            onnx_rt_dict[input_name] = input_data

        pred = self.caffe_net.forward()
        caffe_outname = self.caffe_net.outputs[0]
        sess = rt.InferenceSession(self.model_def.SerializeToString())
        outname = [output.name for output in sess.get_outputs()]
        res = sess.run(outname, onnx_rt_dict)
        np.testing.assert_allclose(pred[caffe_outname], res[0], rtol=1e-03, atol=1e-05)

    def _print_inplace_dict(self):
        import json

        print(json.dumps(self.inplace_dict, indent=4))

    def _update_inplace_dict(self, layer, input_name, output_name):
        assert len(layer.top) == 1
        if layer.top[0] not in self.inplace_dict.keys():
            self.inplace_dict[layer.top[0]] = {}

        self.inplace_dict[layer.top[0]][layer.name] = {}
        self.inplace_dict[layer.top[0]][layer.name]["orig_input"] = layer.top[0]
        self.inplace_dict[layer.top[0]][layer.name]["orig_output"] = layer.top[0]
        self.inplace_dict[layer.top[0]][layer.name]["new_input"] = input_name
        self.inplace_dict[layer.top[0]][layer.name]["new_output"] = output_name

    def _load_caffe_prototxt(self):
        net = caffe_pb2.NetParameter()
        with open(self.proto_file) as f:
            text_format.Merge(f.read(), net)

        return net

    def _load_caffe_weight(self):
        weight = caffe_pb2.NetParameter()
        with open(self.weight_file, "rb") as f:
            weight.ParseFromString(f.read())

        return weight

    def _load_net(self):
        caffe_net = caffe.Net(self.proto_file, caffe.TEST, weights=self.weight_file)

        return caffe_net

    def _get_net_length(self):

        return len(self.caffe_net._layer_names)

    def _get_layer_index(self, layer):
        idx = list(self.caffe_net._layer_names).index(layer.name)

        return idx

    def _param_to_numpy(self, params):
        params_numpy = [p.data for p in params]

        return params_numpy

    def _freeze(self):
        logging.info("removing not constant initializers from model")
        inputs = self.model_def.graph.input
        name_to_input = {}
        for input in inputs:
            name_to_input[input.name] = input

        for initializer in self.model_def.graph.initializer:
            if initializer.name in name_to_input:
                inputs.remove(name_to_input[initializer.name])
