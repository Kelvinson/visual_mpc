import numpy as np
import os
from python_visual_mpc.visual_mpc_core.agent.benchmarking_agent import BenchmarkAgent
from python_visual_mpc.visual_mpc_core.envs.sawyer_robot.autograsp_sawyer_env import AutograspSawyerEnv
from python_visual_mpc.visual_mpc_core.algorithm.custom_samplers.folding_sampler import FoldingSampler
from python_visual_mpc.visual_mpc_core.algorithm.cem_controller_vidpred import CEM_Controller_Vidpred
BASE_DIR = '/'.join(str.split(__file__, '/')[:-1])
current_dir = os.path.dirname(os.path.realpath(__file__))


env_params = {
    'lower_bound_delta': [0, 0., -0.01, 265 * np.pi / 180 - np.pi/2, 0],
    'upper_bound_delta': [0, -0.15, -0.01, 0., 0],
    'normalize_actions': True,
    'gripper_joint_thresh': 0.999856,
    'rand_drop_reset': False,
    'start_box': [1, 1, 0.7],
    'reset_before_eval': True,
    'video_save_dir':  '/home/sudeep/Desktop/exp_rec/',   # doesn't matter what you put here (should make this more elegant)
    'zthresh': 0.05   # gripper only closes very close to ground
}

agent = {'type' : BenchmarkAgent,
         'env': (AutograspSawyerEnv, env_params),
         'data_save_dir': BASE_DIR,
         'T' : 15,  #number of commands per episodes (issued at control_rate / substeps HZ)
         'image_height': 48,
         'image_width': 64,
         'current_dir': current_dir,
         'ntask': 2
         }

policy = {
    'verbose': True,
    'type': CEM_Controller_Vidpred,
    'replan_interval': 15,
    'num_samples': [600, 300],
    'custom_sampler': FoldingSampler,
    'selection_frac': 0.05,
    # 'predictor_propagation': True,   # use the model get the designated pixel for the next step!
    'initial_std': 0.005,
    'initial_std_lift': 0.05,  # std dev. in xy
    'state_append': [0.41, 0.25, 0.166]
}

config = {
    'traj_per_file':128,
    'current_dir' : current_dir,
    'save_data': True,
    'save_raw_images' : True,
    'start_index':0,
    'end_index': 30000,
    'agent': agent,
    'policy': policy,
    'ngroup': 1000,
    'nshuffle' : 200
}
