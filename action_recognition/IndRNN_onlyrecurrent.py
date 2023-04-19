# -*- coding: utf-8 -*-
"""
This code is to implement the IndRNN (only the recurrent part). The code is based on the Lasagne implementation of RecurrentLayer.

Since this only contains the recurrent part of IndRNN, fully connected layers or convolutional layers are needed before it.

Please cite the following paper if you find it useful.

Shuai Li, Wanqing Li, Chris Cook, Ce Zhu, and Yanbo Gao. "Independently Recurrent Neural Network (IndRNN): Building A Longer and Deeper RNN." CVPR 2018.
@article{li2018independently,
  title={Independently Recurrent Neural Network (IndRNN): Building A Longer and Deeper RNN},
  author={Li, Shuai and Li, Wanqing and Cook, Chris and Zhu, Ce and Gao, Yanbo},
  booktitle={CVPR2018},
  year={2018}
}
"""

import numpy as np
import theano
import theano.tensor as T
import lasagne.nonlinearities as nonlinearities
import lasagne.init as init
from lasagne.utils import unroll_scan

from lasagne.layers import MergeLayer, Layer
from lasagne.layers import InputLayer
from lasagne.layers import DenseLayer
from lasagne.layers import helper
import lasagne

__all__ = [
    "onlyRecurrentLayer",
    "MulLayer",
    "IndRNNLayer_onlyrecurrent"
]



class MulLayer(lasagne.layers.Layer):
    def __init__(self, incoming,  W=lasagne.init.Normal(0.01), **kwargs):
        super(MulLayer, self).__init__(incoming, **kwargs)
        num_inputs = self.input_shape[1]
        self.W = self.add_param(W, (num_inputs, ), name='W')

    def get_output_for(self, input, **kwargs):
        return input * self.W

    def get_output_shape_for(self, input_shape):
        return input_shape#(input_shape[0], self.num_units)




class onlyRecurrentLayer(MergeLayer):
    """
    This is slightly different from the CustomRecurrentLayer of Lasagne by removing the computation of input.
    """
    def __init__(self, incoming, input_to_hidden, hidden_to_hidden,
                 nonlinearity=nonlinearities.rectify,
                 hid_init=init.Constant(0.),
                 backwards=False,
                 learn_init=False,
                 gradient_steps=-1,
                 grad_clipping=0,
                 unroll_scan=False,
                 precompute_input=True,
                 mask_input=None,
                 only_return_final=False,
                 **kwargs):

        # This layer inherits from a MergeLayer, because it can have three
        # inputs - the layer input, the mask and the initial hidden state.  We
        # will just provide the layer input as incomings, unless a mask input
        # or initial hidden state was provided.
        incomings = [incoming]
        self.mask_incoming_index = -1
        self.hid_init_incoming_index = -1
        if mask_input is not None:
            incomings.append(mask_input)
            self.mask_incoming_index = len(incomings)-1
        if isinstance(hid_init, Layer):
            incomings.append(hid_init)
            self.hid_init_incoming_index = len(incomings)-1

        super(onlyRecurrentLayer, self).__init__(incomings, **kwargs)

        input_to_hidden_in_layers = \
            [layer for layer in helper.get_all_layers(input_to_hidden)
             if isinstance(layer, InputLayer)]
        if len(input_to_hidden_in_layers) != 1:
            raise ValueError(
                '`input_to_hidden` must have exactly one InputLayer, but it '
                'has {}'.format(len(input_to_hidden_in_layers)))

        hidden_to_hidden_in_lyrs = \
            [layer for layer in helper.get_all_layers(hidden_to_hidden)
             if isinstance(layer, InputLayer)]
        if len(hidden_to_hidden_in_lyrs) != 1:
            raise ValueError(
                '`hidden_to_hidden` must have exactly one InputLayer, but it '
                'has {}'.format(len(hidden_to_hidden_in_lyrs)))
        hidden_to_hidden_in_layer = hidden_to_hidden_in_lyrs[0]

        self.input_to_hidden = input_to_hidden
        self.hidden_to_hidden = hidden_to_hidden
        self.learn_init = learn_init
        self.backwards = backwards
        self.gradient_steps = gradient_steps
        self.grad_clipping = grad_clipping
        self.unroll_scan = unroll_scan
        self.precompute_input = precompute_input
        self.only_return_final = only_return_final
        

        if unroll_scan and gradient_steps != -1:
            raise ValueError(
                "Gradient steps must be -1 when unroll_scan is true.")

        # Retrieve the dimensionality of the incoming layer
        input_shape = self.input_shapes[0]

        if nonlinearity is None:
            self.nonlinearity = nonlinearities.identity
        else:
            self.nonlinearity = nonlinearity

        # Initialize hidden state
        if isinstance(hid_init, Layer):
            self.hid_init = hid_init
        else:
            self.hid_init = self.add_param(
                hid_init, (1,) + hidden_to_hidden.output_shape[1:],
                name="hid_init", trainable=learn_init, regularizable=False)

    def get_params(self, **tags):
        # Get all parameters from this layer, the master layer
        params = super(onlyRecurrentLayer, self).get_params(**tags)
        # Combine with all parameters from the child layers
        params += helper.get_all_params(self.input_to_hidden, **tags)
        params += helper.get_all_params(self.hidden_to_hidden, **tags)
        return params

    def get_output_shape_for(self, input_shapes):
        # The shape of the input to this layer will be the first element
        # of input_shapes, whether or not a mask input is being used.
        input_shape = input_shapes[0]
        # When only_return_final is true, the second (sequence step) dimension
        # will be flattened
        if self.only_return_final:
            return (input_shape[0],) + self.hidden_to_hidden.output_shape[1:]
        # Otherwise, the shape will be (n_batch, n_steps, trailing_dims...)
        else:
            return ((input_shape[0], input_shape[1]) +
                    self.hidden_to_hidden.output_shape[1:])

    def get_output_for(self, inputs, **kwargs):
        # Retrieve the layer input
        input = inputs[0]
        # Retrieve the mask when it is supplied
        mask = None
        hid_init = None
        if self.mask_incoming_index > 0:
            mask = inputs[self.mask_incoming_index]
        if self.hid_init_incoming_index > 0:
            hid_init = inputs[self.hid_init_incoming_index]

        # Input should be provided as (n_batch, n_time_steps, n_features)
        # but scan requires the iterable dimension to be first
        # So, we need to dimshuffle to (n_time_steps, n_batch, n_features)
        #input = input.dimshuffle(1, 0, *range(2, input.ndim))
        seq_len, num_batch = input.shape[0], input.shape[1]

        # We will always pass the hidden-to-hidden layer params to step
        non_seqs = helper.get_all_params(self.hidden_to_hidden)

        # Create single recurrent computation step function
        def step(input_n, hid_previous, *args):
            # Compute the hidden-to-hidden activation
            hid_pre = helper.get_output(
                self.hidden_to_hidden, hid_previous, **kwargs)

            hid_pre += input_n

            # Clip gradients
            if self.grad_clipping:
                hid_pre = theano.gradient.grad_clip(
                    hid_pre, -self.grad_clipping, self.grad_clipping)

            return self.nonlinearity(hid_pre)

        def step_masked(input_n, mask_n, hid_previous, *args):
            # Skip over any input with mask 0 by copying the previous
            # hidden state; proceed normally for any input with mask 1.
            hid = step(input_n, hid_previous, *args)
            hid_out = T.switch(mask_n, hid, hid_previous)
            return [hid_out]

        if mask is not None:
            mask = mask.dimshuffle(1, 0, 'x')
            sequences = [input, mask]
            step_fun = step_masked
        else:
            sequences = input
            step_fun = step

        if not isinstance(self.hid_init, Layer):
            # The code below simply repeats self.hid_init num_batch times in
            # its first dimension.  Turns out using a dot product and a
            # dimshuffle is faster than T.repeat.
            dot_dims = (list(range(1, self.hid_init.ndim - 1)) +
                        [0, self.hid_init.ndim - 1])
            hid_init = T.dot(T.ones((num_batch, 1)),
                             self.hid_init.dimshuffle(dot_dims))

        if self.unroll_scan:
            # Retrieve the dimensionality of the incoming layer
            input_shape = self.input_shapes[0]
            # Explicitly unroll the recurrence instead of using scan
            hid_out = unroll_scan(
                fn=step_fun,
                sequences=sequences,
                outputs_info=[hid_init],
                go_backwards=self.backwards,
                non_sequences=non_seqs,
                n_steps=input_shape[1])[0]
        else:
            # Scan op iterates over first dimension of input and repeatedly
            # applies the step function
            hid_out = theano.scan(
                fn=step_fun,
                sequences=sequences,
                go_backwards=self.backwards,
                outputs_info=[hid_init],
                non_sequences=non_seqs,
                truncate_gradient=self.gradient_steps,
                strict=True)[0]

        # When it is requested that we only return the final sequence step,
        # we need to slice it out immediately after scan is applied
        if self.only_return_final:
            hid_out = hid_out[-1]
        else:
            # dimshuffle back to (n_batch, n_time_steps, n_features))
            #hid_out = hid_out.dimshuffle(1, 0, *range(2, hid_out.ndim))

            # if scan is backward reverse the output
            if self.backwards:
                hid_out = hid_out[::-1,:]

        return hid_out


class IndRNNLayer_onlyrecurrent(onlyRecurrentLayer):

    def __init__(self, incoming, num_units,
                 #W_in_to_hid=init.Uniform(),
                 W_hid_to_hid=init.Uniform(),
                 #b=init.Constant(0.),
                 nonlinearity=nonlinearities.rectify,
                 hid_init=init.Constant(0.),
                 backwards=False,
                 learn_init=False,
                 gradient_steps=-1,
                 grad_clipping=0,
                 unroll_scan=False,
                 precompute_input=True,
                 mask_input=None,
                 only_return_final=False,
                 **kwargs):

        if isinstance(incoming, tuple):
            input_shape = incoming
        else:
            input_shape = incoming.output_shape
        # Retrieve the supplied name, if it exists; otherwise use ''
        if 'name' in kwargs:
            basename = kwargs['name'] + '.'
            # Create a separate version of kwargs for the contained layers
            # which does not include 'name'
            layer_kwargs = dict((key, arg) for key, arg in kwargs.items()
                                if key != 'name')
        else:
            basename = ''
            layer_kwargs = kwargs
        # We will be passing the input at each time step to the dense layer,
        # so we need to remove the second dimension (the time dimension)
        in_to_hid = InputLayer(input_shape)
        
#         in_to_hid = DenseLayer(InputLayer((None,) + input_shape[2:]),
#                                num_units, W=W_in_to_hid, b=b,
#                                nonlinearity=None,
#                                name=basename + 'input_to_hidden',
#                                **layer_kwargs)        
        # The hidden-to-hidden layer expects its inputs to have num_units
        # features because it recycles the previous hidden state
        
        hid_to_hid = MulLayer(InputLayer((None, num_units)),
                                 W=W_hid_to_hid, 
                                name=basename + 'hidden_to_hidden',
                                **layer_kwargs)
#         hid_to_hid = DenseLayer(InputLayer((None, num_units)),
#                                 num_units, W=W_hid_to_hid, b=None,
#                                 nonlinearity=None,
#                                 name=basename + 'hidden_to_hidden',
#                                 **layer_kwargs)

        # Make child layer parameters intuitively accessible
        #self.W_in_to_hid = in_to_hid.W
        self.W_hid_to_hid = hid_to_hid.W
        #self.b = in_to_hid.b

        # Just use the CustomRecurrentLayer with the DenseLayers we created
        super(IndRNNLayer_onlyrecurrent, self).__init__(
            incoming, in_to_hid, hid_to_hid, nonlinearity=nonlinearity,
            hid_init=hid_init, backwards=backwards, learn_init=learn_init,
            gradient_steps=gradient_steps,
            grad_clipping=grad_clipping, unroll_scan=unroll_scan,
            precompute_input=precompute_input, mask_input=mask_input,
            only_return_final=only_return_final, **kwargs)
