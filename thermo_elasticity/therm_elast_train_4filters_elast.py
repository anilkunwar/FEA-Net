import numpy as np
import scipy.io as sio
import tensorflow as tf
import matplotlib.pyplot as plt
from tqdm import tqdm


class FEA_Net_h():
    def __init__(self, data, cfg):
        # set learning rate
        self.cfg = cfg

        # data related
        self.num_node = data['num_node']
        self.E, self.mu, self.k, self.alpha = self.rho = data['rho'] #

        # 3 dimensional in and out, defined on the nodes
        self.load_pl = tf.placeholder(tf.float32, shape=(None, data['num_node'], data['num_node'], 3))
        self.resp_pl = tf.placeholder(tf.float32, shape=(None, data['num_node'], data['num_node'], 3))

        # get filters
        self.get_w_matrix()
        self.load_pred = self.forward_pass()


    def get_w_matrix(self):
        self.get_w_matrix_elast()
        self.get_w_matrix_thermal()
        self.get_w_matrix_coupling()
        self.apply_physics_constrain(cfg)

    def apply_physics_constrain(self, cfg):
        # known physics
        self.wtt_tf = tf.constant(self.wtt_ref)
        self.wtx_tf = tf.constant(self.wtx_ref)
        self.wty_tf = tf.constant(self.wty_ref)
        self.wxt_tf = tf.constant(self.wxt_ref)
        self.wyt_tf = tf.constant(self.wyt_ref)

        # unknown physics
        self.wxx_np = np.zeros_like(self.wxx_ref)
        self.wyy_np = np.zeros_like(self.wyy_ref)
        self.wxy_np = np.zeros_like(self.wxy_ref)
        self.wyx_np = np.zeros_like(self.wyx_ref)

        # TF variable vector
        self.trainable_var_np = np.concatenate([self.wxx_np.flatten(),
                                                self.wyy_np.flatten(),
                                                self.wxy_np.flatten(),
                                                self.wyx_np.flatten()],0)
        self.trainable_var_pl = tf.placeholder(tf.float32, shape=(9*4,))

        wxx_np, wyy_np, wxy_np, wyx_np = tf.split(self.trainable_var_pl,4)
        self.wxx_tf = tf.reshape(wxx_np,(3,3,1,1))
        self.wyy_tf = tf.reshape(wyy_np,(3,3,1,1))
        self.wxy_tf = tf.reshape(wxy_np,(3,3,1,1))
        self.wyx_tf = tf.reshape(wyx_np,(3,3,1,1))

        # add constrains
        self.singula_penalty = tf.abs(tf.reduce_sum(self.wxx_tf)) \
                               + tf.abs(tf.reduce_sum(self.wyy_tf)) \
                               + tf.abs(tf.reduce_sum(self.wxy_tf))\
                               + tf.abs(tf.reduce_sum(self.wyx_tf))
        def get_sym_penalty(w):
            return tf.abs(tf.reduce_sum((w[0,0,0,0]-w[2,2,0,0])**2)) \
                                +tf.abs(tf.reduce_sum((w[1,0,0,0]-w[1,2,0,0])**2)) \
                                +tf.abs(tf.reduce_sum((w[0,1,0,0]-w[2,1,0,0])**2)) \
                                +tf.abs(tf.reduce_sum((w[0,2,0,0]-w[2,0,0,0])**2))

        self.symmetry_penalty = get_sym_penalty(self.wxx_tf)\
                               + get_sym_penalty(self.wyy_tf) \
                               + get_sym_penalty(self.wxy_tf)\
                               + get_sym_penalty(self.wyx_tf)

        # self.E = tf.clip_by_value(self.E, 0, 1)
        # self.mu = tf.clip_by_value(self.mu, 0, 0.5)

        # tf.nn.conv2d filter shape: [filter_height, filter_width, in_channels, out_channels]
        self.w_filter = tf.concat([tf.concat([self.wxx_tf, self.wxy_tf, self.wxt_tf],2),
                                   tf.concat([self.wyx_tf, self.wyy_tf, self.wyt_tf],2),
                                   tf.concat([self.wtx_tf, self.wty_tf, self.wtt_tf],2)],
                                  3)

    def get_w_matrix_coupling(self):
        E, v = self.E, self.mu
        alpha = self.alpha
        self.wtx_ref = np.zeros((3,3,1,1), dtype='float32')
        self.wty_ref = np.zeros((3,3,1,1), dtype='float32')
        coef = E * alpha / (6*(v-1)) / 400 *1e6
        self.wxt_ref = coef * np.asarray([[1, 0, -1],
                                      [4, 0, -4],
                                      [1, 0, -1]]
                                     , dtype='float32').reshape(3,3,1,1)

        self.wyt_ref = coef * np.asarray([[-1, -4, -1],
                                      [0, 0, 0],
                                      [1, 4, 1]]
                                     , dtype='float32').reshape(3,3,1,1)

    def get_w_matrix_thermal(self):
        w = -1/3. * self.k * np.asarray([[1., 1., 1.], [1., -8., 1.], [1., 1., 1.]])
        w = np.asarray(w, dtype='float32')
        self.wtt_ref = w.reshape(3,3,1,1)

    def get_w_matrix_elast(self):
        E, mu = self.E, self.mu
        cost_coef = E / 16. / (1 - mu ** 2)
        wxx = cost_coef * np.asarray([
            [-4 * (1 - mu / 3.), 16 * mu / 3., -4 * (1 - mu / 3.)],
            [-8 * (1 + mu / 3.), 32. * (1 - mu / 3.), -8 * (1 + mu / 3.)],
            [-4 * (1 - mu / 3.), 16 * mu / 3., -4 * (1 - mu / 3.)],
        ], dtype='float32')

        wxy = wyx = cost_coef * np.asarray([
            [2 * (mu + 1), 0, -2 * (mu + 1)],
            [0, 0, 0],
            [-2 * (mu + 1), 0, 2 * (mu + 1)],
        ], dtype='float32')

        wyy = cost_coef * np.asarray([
            [-4 * (1 - mu / 3.), -8 * (1 + mu / 3.), -4 * (1 - mu / 3.)],
            [16 * mu / 3., 32. * (1 - mu / 3.), 16 * mu / 3.],
            [-4 * (1 - mu / 3.), -8 * (1 + mu / 3.), -4 * (1 - mu / 3.)],
        ], dtype='float32')

        self.wxx_ref = wxx.reshape(3,3,1,1)
        self.wxy_ref = wxy.reshape(3,3,1,1)
        self.wyx_ref = wyx.reshape(3,3,1,1)
        self.wyy_ref = wyy.reshape(3,3,1,1)

    def boundary_padding(self,x):
        ''' special symmetric boundary padding '''
        left = x[:, :, 1:2, :]
        right = x[:, :, -2:-1, :]
        upper = tf.concat([x[:, 1:2, 1:2, :], x[:, 1:2, :, :], x[:, 1:2, -2:-1, :]], 2)
        down = tf.concat([x[:, -2:-1, 1:2, :], x[:, -2:-1, :, :], x[:, -2:-1, -2:-1, :]], 2)
        padded_x = tf.concat([left, x, right], 2)
        padded_x = tf.concat([upper, padded_x, down], 1)
        return padded_x

    def forward_pass(self):
        padded_resp = self.boundary_padding(self.resp_pl)  # for boundary consideration
        wx = tf.nn.conv2d(input=padded_resp, filter=self.w_filter, strides=[1, 1, 1, 1], padding='VALID')
        return wx

    def get_loss(self):
        self.diff = self.load_pred - self.load_pl
        self.diff_no_on_bc = self.diff[:,1:-1,1:-1,:]
        self.l1_error = tf.reduce_mean(self.diff_no_on_bc**2)
        self.loss = self.l1_error# + self.symmetry_penalty #+ self.singula_penalty
        return self.loss

    def get_grad(self):
        self.rho_grads = tf.gradients(self.loss, self.trainable_var_pl)
        return self.rho_grads

    def get_hessian(self):
        self.rho_hessian = tf.hessians(self.loss, self.trainable_var_pl)
        return self.rho_hessian



class Evaluator(object):
    def __init__(self, model, data, cfg):
        self.model = model
        self.data = data
        self.cfg = cfg
        self.init_w = np.zeros((3,3,1,1))

        self.loss_value = None
        self.grads_value = None

        self.loss_tf = self.model.get_loss()
        self.grad_tf = self.model.get_grad()
        self.hessian_tf = self.model.get_hessian()
        self.initial_graph()

    def initial_graph(self):
        # initialize
        FLAGS = tf.app.flags.FLAGS
        tfconfig = tf.ConfigProto(
            allow_soft_placement=True,
            log_device_placement=True,
        )
        tfconfig.gpu_options.allow_growth = True
        self.sess = tf.Session(config=tfconfig)
        init = tf.global_variables_initializer()
        self.sess.run(init)

    def loss(self, w):
        self.feed_dict = {self.model.load_pl: data['train_load'],
                          self.model.resp_pl: data['train_resp'],
                          self.model.trainable_var_pl: w}
        self.loss_value = self.sess.run(self.loss_tf, self.feed_dict).astype('float64')
        return self.loss_value

    def grads(self, w):
        self.feed_dict = {self.model.load_pl: data['train_load'],
                          self.model.resp_pl: data['train_resp'],
                          self.model.trainable_var_pl: w}
        self.grads_value = self.sess.run(self.grad_tf, self.feed_dict)[0].flatten().astype('float64')
        return self.grads_value

    def hessian(self, w):
        self.feed_dict = {self.model.load_pl: data['train_load'],
                          self.model.resp_pl: data['train_resp'],
                          self.model.trainable_var_pl: w}
        self.hessian_value = self.sess.run(self.hessian_tf, self.feed_dict)[0].astype('float64')
        return self.hessian_value

    def pred(self,w):
        feed_dict = {self.model.load_pl: data['train_load'],
                      self.model.resp_pl: data['train_resp'],
                      self.model.trainable_var_pl: w.astype('float32')}
        pred_value = self.sess.run(self.model.load_pred, feed_dict)
        return pred_value

    def run_BFGS(self):
        from scipy.optimize import fmin_l_bfgs_b
        x, min_val, info = fmin_l_bfgs_b(self.loss, self.init_w.flatten(),
                                         fprime=self.grads, maxiter=200, maxfun=200,
                                         disp= True)
        print('    loss: {}'.format(min_val))
        pass

    def run_newton(self):
        from scipy.optimize import minimize
        self.result = minimize(self.loss, self.model.trainable_var_np, method=self.cfg['opt_method'],
                          jac=self.grads, hess=self.hessian,
                          options={'gtol': 1e-3, 'disp': True})
        return self.result

    def visualize(self, w):
        pred_value = self.pred(w)
        plt.figure(figsize=(6, 6))
        idx = 0  # which data to visualize
        for i in range(3):
            plt.subplot(4, 3, i + 1)
            plt.imshow(self.data['test_resp'][idx, 1:-1, 1:-1, i])
            plt.colorbar()
            plt.subplot(4, 3, 3 + i + 1)
            plt.imshow(self.data['test_load'][idx, 1:-1, 1:-1, i])
            plt.colorbar()
            plt.subplot(4, 3, 6 + i + 1)
            plt.imshow(pred_value[idx, 1:-1, 1:-1, i])
            plt.colorbar()
            plt.subplot(4, 3, 9 + i + 1)
            plt.imshow(self.data['test_load'][idx, 1:-1, 1:-1, i] - pred_value[idx, 1:-1, 1:-1, i])
            plt.colorbar()
        plt.show()

    def est_rho(self, mat):
        mat = mat.reshape(4,3,3)
        mat[0]

def load_data():
    num_node = 37
    # Purely thermal
    # data = sio.loadmat('2D_thermoelastic_36by36_xy_fixed_single_data5.mat')

    # purely structural
    #data = sio.loadmat('/home/hope-yao/Documents/MG_net/data/heat_transfer/Downloads/2D_thermoelastic_36by36_xy_fixed_single_data2.mat')

    # coupled loading
    data = sio.loadmat('2D_thermoelastic_36by36_xy_fixed_single_data4.mat')

    load = np.expand_dims(np.stack([-data['fx'], -data['fy'], data['ftem']], -1), 0).astype('float32')
    resp = np.expand_dims(np.stack([data['ux']*1e6, data['uy']*1e6, data['utem']], -1), 0).astype('float32')
    rho = [212e3, 0.288, 16., 12e-6] # E, mu, k, alpha

    train_load = load
    train_resp = resp
    test_load = load
    test_resp = resp
    data = {'num_node': num_node,
            'rho': rho,
            'train_load': train_load,
            'train_resp': train_resp,
            'test_load': test_load,
            'test_resp': test_resp,
            }

    return data

if __name__ == "__main__":
    import os
    os.environ['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'
    os.environ['CUDA_VISIBLE_DEVICES'] = '1'
    cfg = {'opt_method': 'Newton-CG', #'trust-ncg',#
           }

    # load data
    data = load_data()

    # build the network
    model = FEA_Net_h(data,cfg)

    # train the network
    evaluator = Evaluator(model, data, cfg)
    result = evaluator.run_newton()
    evaluator.visualize(result.x)

    for i in range(4):
        mat = result.x[9*i:9*(i+1)]
        print(mat.reshape(3,3))
        print(np.sum(mat))

    evaluator.est_rho(result.x)
