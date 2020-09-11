'''
Created on 2020-04-22 19:50:46
Last modified on 2020-09-11 09:57:42
Python 2.7.16
v0.1

@author: L. F. Pereira (lfpereira@fe.up.pt)
'''


# imports

# standard library
import os
from collections import OrderedDict
import time
import pickle
import multiprocessing as mp
import traceback
import shutil

# third party
import numpy as np

# local library
from f3das.misc.file_handling import verify_existing_name
from f3das.misc.file_handling import get_unique_file_by_ext


# TODO: interrupt simulations

# create main file

def create_main_file(example_name, data, pkl_filename='DoE.pkl'):
    '''
    Create file where all the information required to run simulations is
    contained.

    Parameters
    ----------
    example_name : str
        Folder name.
    data : dict
        MUST contain:
        * 'doe_variables': dict with variables of the DoE (generated by
        design of experiments) with lower and upper bounds.
        * 'points': pandas DataFrame with design of experiments.
        * 'fixed_variables': model input variables that are kept fix during
        the design of experiments.
        * 'additional_variables': dict with model input variables that change
        in each DoE but are not controlled by the design of experiments.
        * 'sim_info': all the information required to create each simulation.
        In particular:
            * 'abstract_model': class that must be called inside the run model.
            * 'sim_info': OrderedDict with all the information required to
            instantiate a model (but that is not a model geometric or material
            variable)
            * 'transform_inputs': function that takes DoE point, fixed variables
            and additional variables and create a dictionary that is used as
            input of the model instance.
    pkl_filename : str
        Name of main file.
    '''

    # add information to manage simulations
    data['run_info'] = {'missing_simuls': list(range(len(data['points']))),
                        'running_simuls': [],
                        'error_simuls': [],
                        'successful_simuls': []}

    # add information to keep odb
    sim_info = data['sim_info']
    data['sim_info'] = sim_info

    # create directory and save pkl file
    # TODO: force error if name already exists
    example_name = verify_existing_name(example_name)
    os.mkdir(example_name)
    with open(os.path.join(example_name, pkl_filename), 'wb') as file:
        pickle.dump(data, file)


# run abaqus

def run_simuls(example_name, n_simuls=None, n_cpus=1,
               points=None, pkl_filename='DoE.pkl', simuls_dir_name='analyses',
               run_module_name='f3das.abaqus.run.run_model',
               keep_odb=True, dump_py_objs=False, abaqus_path='abaqus',
               gui=False):
    '''
    IMPORTANT: if number cpus>1 (parallel simulations, not simulation in
    parallel), function must be inside "if __name__='__main__':" in the script.

    Parameters
    ----------
    n_simuls : int or None
        Number of simulations to run. Ignored if 'points' is not None.
        'missing_simuls' in the main file are considered here. Runs all the
        missing simulations if None. Order of 'missing_simuls' is considered.
    n_cpus : int
        Number of simultaneous processes. If job's 'n_cpus'>1, it is automatically
        set to 1 (for now, it is not possible to run several multi-process
        simulations simultaneously).
    points : array or None
        DoE points to run. If None, 'n_simuls' are run. Simulations with
        folders already created are run again (so, be careful!).
    pkl_filename : str
        Main file name.
    keep_odb : bool
        Keep odb after simulation? If yes, make sure you have enough storage.
    dump_py_objs : bool
        Store Python objects that were used to create and run the numerical
        simulations? If yes, a file with extension 'pkl_abq' is created.
        Specially useful for debugging.
    '''

    # TODO: zip odb

    # create analyses dir
    dir_full_path = os.path.join(example_name, simuls_dir_name)
    if not os.path.exists(dir_full_path):
        os.mkdir(dir_full_path)

    # get data
    with open(os.path.join(example_name, pkl_filename), 'rb') as file:
        data = pickle.load(file)

    # points or n_simuls?
    missing_simuls = data['run_info']['missing_simuls']
    if points is not None:
        # TODO: delete folders if exist; not here, force creation of the folder afterwards
        pass
    else:
        n_simuls = len(missing_simuls) if n_simuls is None else n_simuls
        points = missing_simuls[:n_simuls]
        missing_simuls = missing_simuls[n_simuls:]

    # update data temporarily
    data['run_info']['missing_simuls'] = missing_simuls
    data['run_info']['running_simuls'].extend(points)
    with open(os.path.join(example_name, pkl_filename), 'wb') as file:
        pickle.dump(data, file)

    try:

        # run in parallel?
        sim_info = data['sim_info']['sim_info']
        n_cpus_sim = np.array([sim['job_info'].get('n_cpus', 1) for sim in sim_info.values()])
        n_cpus = 1 if np.prod(n_cpus_sim) != 1 else n_cpus

        # create pkl for each doe
        _create_DoE_sim_info(example_name, points, simuls_dir_name=simuls_dir_name,
                             pkl_filename=pkl_filename, keep_odb=keep_odb,
                             dump_py_objs=dump_py_objs)

        # run
        if n_cpus > 1:

            # distribute points
            points = sorted(points)
            points_cpus = []
            for i in range(n_cpus):
                points_cpus.append(points[i::n_cpus])

            # start pool
            pool = mp.Pool(n_cpus)

            # run simuls
            for i, points in enumerate(points_cpus):
                wait_time = i * 5
                pool.apply_async(_run_simuls_sequentially,
                                 args=(example_name, points, wait_time,
                                       run_module_name, simuls_dir_name,
                                       abaqus_path))
            # close pool and wait process completion
            pool.close()
            pool.join()

        else:
            _run_simuls_sequentially(example_name, points,
                                     run_module_name=run_module_name,
                                     simuls_dir_name=simuls_dir_name,
                                     abaqus_path=abaqus_path, gui=gui)
    except:
        traceback.print_exc()

    finally:
        # based on points, reupdate data['run_info']
        error_simuls_, successful_simuls_ = get_updated_simuls_state(
            example_name, simuls_dir_name, points)

        points_ = list(set(points) - set(error_simuls_) - set(successful_simuls_))
        data['run_info']['missing_simuls'].extend(points_)
        data['run_info']['missing_simuls'].sort()

        data['run_info']['error_simuls'].extend(error_simuls_)
        data['run_info']['error_simuls'].sort()

        data['run_info']['successful_simuls'].extend(successful_simuls_)
        data['run_info']['successful_simuls'].sort()

        running_simuls = data['run_info']['running_simuls']
        data['run_info']['running_simuls'] = sorted(list(set(running_simuls).difference(set(points))))

        with open(os.path.join(example_name, pkl_filename), 'wb') as file:
            pickle.dump(data, file)


def _create_DoE_sim_info(example_name, points, simuls_dir_name='analyses',
                         pkl_filename='DoE.pkl', keep_odb=True,
                         dump_py_objs=False,):

    # get data
    with open(os.path.join(example_name, pkl_filename), 'rb') as file:
        data = pickle.load(file)
    datapoints = data['points']
    sim_info = data['sim_info']
    transform_inputs = sim_info.get('transform_inputs', None)
    fixed_variables = data.get('fixed_variables', {})
    additional_variables = data.get('additional_variables', {})

    # variables to save
    abstract_model = sim_info['abstract_model']

    # deal with subroutines
    subroutine_names = []
    for sim_info_ in sim_info['sim_info'].values():
        subroutine_name = sim_info_['job_info'].get('userSubroutine', None)
        if subroutine_name:
            subroutine_loc_ls = subroutine_name.split('.')
            subroutine_loc = '{}.{}'.format(os.path.join(*subroutine_loc_ls[:-1]), subroutine_loc_ls[-1])
            subroutine_names.append((subroutine_loc, '.'.join(subroutine_loc_ls[-2::])))
            sim_info_['job_info']['userSubroutine'] = subroutine_names[-1][1]

    # create pkl files
    dir_full_path = os.path.join(example_name, simuls_dir_name)
    for point in points:
        doe_dir_name = os.path.join(dir_full_path, 'DoE_point{}'.format(point))
        os.mkdir(doe_dir_name)

        # dict with all the variables
        variables = datapoints.loc[point].to_dict()
        variables.update(fixed_variables)
        for key, value in additional_variables.items():
            variables[key] = float(value[point])
            # TODO: what if list?

        # if required, transform inputs
        if callable(transform_inputs):
            variables = transform_inputs(variables)

        # create and dump dict
        data = OrderedDict({'abstract_model': abstract_model,
                            'variables': variables,
                            'sim_info': sim_info['sim_info'],
                            'keep_odb': keep_odb,
                            'dump_py_objs': dump_py_objs,
                            'success': None})

        with open(os.path.join(doe_dir_name, 'simul.pkl'), 'wb') as file:
            pickle.dump(data, file, protocol=2)

        # copy subroutine
        if subroutine_names:
            for subroutine_name in subroutine_names:
                shutil.copyfile(subroutine_name[0], os.path.join(doe_dir_name, subroutine_name[1]))


def _run_simuls_sequentially(example_name, points, wait_time=0,
                             run_module_name='f3das.abaqus.run.run_model',
                             simuls_dir_name='analyses', abaqus_path='abaqus',
                             gui=False):
    '''

    Parameters
    ----------
    # TODO: change docstrings

    '''

    # initialization
    time.sleep(wait_time)

    # create run filename
    run_filename = verify_existing_name('_temp.py')
    lines = ['import runpy',
             'import os',
             'import sys',
             'initial_wd = os.getcwd()',
             'sys.path.append(initial_wd)',
             'points = %s' % points,
             "sim_dir = r'%s'" % os.path.join(example_name, simuls_dir_name),
             'for point in points:',
             "\tos.chdir('%s' % os.path.join(sim_dir, 'DoE_point%i' % point))",
             "\trunpy.run_module('%s', run_name='__main__')" % run_module_name,
             '\tos.chdir(initial_wd)']
    with open(run_filename, 'w') as f:
        for line in lines:
            f.write(line + '\n')

    # open abaqus and run module
    gui_ = 'script' if gui else 'noGUI'
    command = '{} cae {}={}'.format(abaqus_path, gui_, run_filename)
    os.system(command)

    # clear temporary run file
    os.remove(run_filename)


def get_updated_simuls_state(example_name, simuls_dir_name, points):
    '''
    Parameters
    ----------
    points : array
        If None, considers all created simulation folders.
    '''

    # initialization
    dir_path = os.path.join(example_name, simuls_dir_name)

    # getting simuls state
    error_simuls = []
    successful_simuls = []
    for point in points:
        folder_path = os.path.join(dir_path, 'DoE_point{}'.format(point))
        if not os.path.exists(folder_path):
            continue

        filename = get_unique_file_by_ext(folder_path, ext='.pkl')
        with open(os.path.join(folder_path, filename), 'rb') as file:
            data = pickle.load(file, encoding='latin1')
        success = data['success']

        if success:
            successful_simuls.append(point)
        elif success is False:
            error_simuls.append(point)

    return error_simuls, successful_simuls


def get_simuls_info(example_name, pkl_filename='DoE.pkl',
                    simuls_dir_name='analyses'):

    # access data
    with open(os.path.join(example_name, pkl_filename), 'rb') as file:
        data = pickle.load(file)

    # running simulations
    running_simuls = data['run_info']['running_simuls']
    error_simuls_, successful_simuls_ = get_updated_simuls_state(
        example_name, simuls_dir_name, running_simuls)
    n_running_simuls = len(running_simuls)
    n_running_simuls_miss = len(list(set(running_simuls) - set(error_simuls_) - set(successful_simuls_)))

    # other simulations
    n_missing_simuls = len(data['run_info']['missing_simuls']) + n_running_simuls_miss
    n_error_simuls = len(data['run_info']['error_simuls']) + len(error_simuls_)
    n_successful_simuls = len(data['run_info']['successful_simuls']) + len(successful_simuls_)
    n_run = n_error_simuls + n_successful_simuls
    n_total = n_missing_simuls + n_run

    # compute information
    if n_running_simuls:
        print('Missing simulations (running): {}/{} ({:.1f}%)'.format(
            n_running_simuls_miss, n_running_simuls,
            n_running_simuls_miss / n_running_simuls * 100))
    print('Missing simulations (total): {}/{} ({:.1f}%)'.format(
        n_missing_simuls, n_total, n_missing_simuls / n_total * 100))
    if n_run:
        print('With errors: {}/{} ({:.1f}%)'.format(
            n_error_simuls, n_run, n_error_simuls / n_run * 100))
        print('Successful: {}/{} ({:.1f}%)'.format(
            n_successful_simuls, n_run, n_successful_simuls / n_run * 100))


def update_run_info(example_name, pkl_filename='DoE.pkl',
                    simuls_dir_name='analyses'):
    '''
    Updates information about simulations. Assumes simulations are not being
    ran. It is supposed to correct possible outdated files due to running of
    simulations in different machines.

    '''
    # TODO: convert errors to missing simuls if required?

    # access data
    with open(os.path.join(example_name, pkl_filename), 'rb') as file:
        data = pickle.load(file)

    # compute information
    points = list(range(len(data['points'])))
    error_simuls, successful_simuls = get_updated_simuls_state(
        example_name, simuls_dir_name, points)
    missing_simuls = list(set(points) - set(error_simuls) - set(successful_simuls))

    # dump information
    data['missing_simuls'] = missing_simuls
    data['running_simuls'] = []
    data['error_simuls'] = error_simuls
    data['successful_simuls'] = successful_simuls
    with open(os.path.join(example_name, pkl_filename), 'wb') as file:
        pickle.dump(data, file)


# function definition

def convert_dict_unicode_str(pickled_dict):
    new_dict = OrderedDict() if type(pickled_dict) is OrderedDict else {}
    for key, value in pickled_dict.items():
        value = _set_converter_flow(value)
        new_dict[str(key)] = value
        print(key, value)

    return new_dict


def convert_iterable_unicode_str(iterable):
    new_iterable = []
    for value in iterable:
        value = _set_converter_flow(value)
        new_iterable.append(value)

    if type(iterable) is tuple:
        new_iterable = tuple(new_iterable)
    elif type(iterable) is set:
        new_iterable = set(new_iterable)

    return new_iterable


def _set_converter_flow(value):

    if type(value) is unicode:
        value = str(value)
    elif type(value) in [OrderedDict, dict]:
        value = convert_dict_unicode_str(value)
    elif type(value) in [list, tuple, set]:
        value = convert_iterable_unicode_str(value)

    return value